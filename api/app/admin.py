"""Admin API behind the console at /admin.

Protected by ADMIN_TOKEN — a separate secret from the apps' API keys, sent as
"Authorization: Bearer <token>". Repeated wrong tokens from one address lock it
out for a while. Every endpoint here is for the operator, not for apps.

    GET  /admin/api/overview           status, models in memory, last 24 h
    GET  /admin/api/models             what's offered, installed, loaded, capable of
    GET  /admin/api/keys               all keys
    POST /admin/api/keys               create one — the key is returned once
    POST /admin/api/keys/{prefix}/revoke
    GET  /admin/api/usage?days=7       per app and API, and per day
    POST /admin/api/playground         chat with a model (Ollama format, streamed)
"""
from __future__ import annotations

import hmac
import re
import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from app.auth import new_key
from app.chat import _forward, _ollama_usage
from app.store import ENVIRONMENTS, ApiKey

_MAX_FAILURES = 10
_LOCKOUT_SECONDS = 600
_failures: dict[str, deque[float]] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


async def require_admin(request: Request) -> None:
    token = request.app.state.settings.ADMIN_TOKEN
    if not token:
        raise HTTPException(503, "The admin console is switched off: set ADMIN_TOKEN in .env and restart.")
    ip = _client_ip(request)
    now = time.monotonic()
    recent = _failures[ip]
    while recent and now - recent[0] > _LOCKOUT_SECONDS:
        recent.popleft()
    if len(recent) >= _MAX_FAILURES:
        raise HTTPException(429, "Too many wrong admin tokens from this address. Try again in 10 minutes.",
                            headers={"Retry-After": str(_LOCKOUT_SECONDS)})
    scheme, _, presented = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(presented.strip().encode(), token.encode()):
        recent.append(now)
        raise HTTPException(401, "Wrong admin token.")


router = APIRouter(prefix="/admin/api", include_in_schema=False, dependencies=[Depends(require_admin)])


def _gb(size: int | None) -> float | None:
    return round(size / 1024**3, 1) if size else None


async def _runtime(request: Request) -> tuple[str, dict[str, dict], dict[str, dict]]:
    """(status, installed by name, loaded by name). Names without ":latest" match too."""
    llm = request.app.state.llm
    try:
        installed = await llm.installed_models()
        loaded = await llm.loaded_models()
    except Exception as exc:
        return f"unreachable: {exc}", {}, {}

    def index(items: list[dict]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for m in items:
            out[m["name"]] = m
            out[m["name"].removesuffix(":latest")] = m
        return out

    return "ok", index(installed), index(loaded)


@router.get("/overview")
async def overview(request: Request) -> dict[str, Any]:
    settings, store = request.app.state.settings, request.app.state.store
    status, installed, loaded = await _runtime(request)
    offered = settings.chat_models() + settings.embedding_models()
    rows = await store.usage_summary(None, 1)
    keys = await store.list_keys()
    loaded_unique = {m["name"]: m for m in loaded.values()}.values()
    return {
        "runtime": status,
        "time": datetime.now(UTC).isoformat(timespec="seconds"),
        "models_offered": len(offered),
        "models_missing": [m for m in offered if m not in installed] if status == "ok" else [],
        "loaded": [{"name": m["name"], "size_gb": _gb(m.get("size")),
                    "on_gpu_pct": round(m.get("size_vram", 0) / m["size"] * 100) if m.get("size") else 0}
                   for m in loaded_unique],
        "roles": {**settings.role_names("chat"), **settings.role_names("embed")},
        "default_model": settings.DEFAULT_MODEL,
        "speech": await request.app.state.speech.health(),
        "keys_active": sum(1 for k in keys if not k.revoked),
        "last_24h": {
            "requests": sum(r["requests"] for r in rows),
            "errors": sum(r["requests"] for r in rows if r["status"] != "ok"),
            "prompt_tokens": sum(r["prompt_tokens"] for r in rows),
            "completion_tokens": sum(r["completion_tokens"] for r in rows),
            "apps": sorted({r["app"] for r in rows}),
        },
    }


@router.get("/models")
async def models(request: Request) -> dict[str, Any]:
    settings, llm = request.app.state.settings, request.app.state.llm
    status, installed, loaded = await _runtime(request)
    roles: dict[str, list[str]] = defaultdict(list)
    for kind in ("chat", "embed"):
        for role, target in settings.role_names(kind).items():
            roles[target].append(role)
    entries = []
    for kind, names in (("chat", settings.chat_models()), ("embed", settings.embedding_models())):
        for name in names:
            entries.append({
                "name": name, "kind": kind,
                "capabilities": sorted(await llm.capabilities(name)),
                "installed": name in installed,
                "size_gb": _gb((installed.get(name) or {}).get("size")),
                "loaded": name in loaded,
                "roles": roles.get(name, []),
                "default": name == settings.DEFAULT_MODEL,
            })
    offered = {e["name"] for e in entries}
    extra = sorted({m["name"] for m in installed.values()}
                   - offered - {f"{n}:latest" for n in offered})
    return {"runtime": status, "models": entries, "installed_not_offered": extra}


class KeyRequest(BaseModel):
    app: str = Field(min_length=2, max_length=32)
    environment: str = "production"
    name: str | None = Field(default=None, max_length=80)
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=10_000)

    @field_validator("app")
    @classmethod
    def _app(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,31}", v):
            raise ValueError("lowercase letters, digits and '-', starting with a letter")
        return v

    @field_validator("environment")
    @classmethod
    def _env(cls, v: str) -> str:
        if v not in ENVIRONMENTS:
            raise ValueError(f"one of: {', '.join(ENVIRONMENTS)}")
        return v


def _key_out(k: ApiKey) -> dict[str, Any]:
    return {"prefix": k.prefix, "app": k.app, "environment": k.environment, "name": k.name,
            "rate_limit_per_minute": k.rate_limit_per_minute,
            "created_at": k.created_at.isoformat() if k.created_at else None,
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            "revoked": k.revoked}


@router.get("/keys")
async def list_keys(request: Request) -> dict[str, Any]:
    keys = await request.app.state.store.list_keys()
    return {"keys": [_key_out(k) for k in sorted(keys, key=lambda k: (k.revoked, k.app, k.created_at))]}


@router.post("/keys")
async def create_key(request: Request, body: KeyRequest) -> dict[str, Any]:
    settings = request.app.state.settings
    key, prefix, key_hash = new_key()
    record = await request.app.state.store.create_key(
        app=body.app, environment=body.environment, name=body.name or f"{body.app}-{body.environment}",
        prefix=prefix, key_hash=key_hash,
        rate_limit_per_minute=body.rate_limit_per_minute or settings.DEFAULT_RATE_LIMIT_PER_MINUTE,
    )
    return {"key": key, **_key_out(record)}


@router.post("/keys/{prefix}/revoke")
async def revoke_key(request: Request, prefix: str) -> dict[str, Any]:
    if not await request.app.state.store.revoke_key(prefix):
        raise HTTPException(404, f"No active key {prefix}.")
    return {"revoked": prefix}


@router.get("/usage")
async def usage(request: Request, days: int = Query(7, ge=1, le=365)) -> dict[str, Any]:
    store = request.app.state.store
    return {"days": days, "summary": await store.usage_summary(None, days),
            "daily": await store.usage_daily(days)}


_ADMIN = ApiKey(id=None, app="admin-console", environment="admin", name="admin console",  # type: ignore[arg-type]
                prefix="admin", rate_limit_per_minute=0, created_at=datetime.now(UTC), scopes=["chat"])


@router.post("/playground")
async def playground(request: Request, body: dict = Body(...)):
    """Chat with any offered model or role. Streams Ollama-format JSON lines; logged as app "admin-console"."""
    return await _forward(_ADMIN, request, body, "/api/chat", "playground",
                          lambda b: b.setdefault("think", False), _ollama_usage,
                          stream_by_default=True, media_type="application/x-ndjson")
@router.get("/docs")
async def docs() -> dict[str, Any]:
    """ Return documentation for all API endpoints with code examples on how to use them. """
    from fastapi.openapi.utils import get_openapi
    from fastapi.responses import JSONResponse

    openapi_schema = get_openapi(
        title="Forgefy Admin API",
        version="1.0.0",
        description="Admin API for managing models, keys, and usage statistics.",
        routes=router.routes,
    )
    return JSONResponse(openapi_schema)