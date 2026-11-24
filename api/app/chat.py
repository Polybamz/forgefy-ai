"""The model service: your models in the three formats clients speak.

    GET  /v1/models                      models and role names on offer, with capabilities
    POST /v1/chat/completions            OpenAI format
    POST /v1/embeddings                  OpenAI format, embeddings
    POST /v1/messages                    Anthropic format
    POST /v1/messages/count_tokens       Anthropic token count (an estimate)
    POST /api/chat                       Ollama format (e.g. Forgefy's backend)
    POST /api/embed                      Ollama format, embeddings
    GET  /api/tags                       models on offer, Ollama format

Requests pass through to the model as sent — system instructions, the
client's own tools, tool results, streaming, JSON mode — so skills, plugins,
MCP servers and agent frameworks keep working on the client's side: Claude
Code and the Claude Agent SDK over the Anthropic format, the OpenAI SDKs,
LangChain, n8n, Open WebUI and editor extensions over the OpenAI format.

What the platform adds: API keys, rate limits, a model allowlist with aliases,
a queue in front of the model, and a usage record per request.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.auth import authenticate
from app.llm import ModelBusy, ModelError
from app.store import ApiKey, UsageRecord

logger = logging.getLogger("ai-api")
router = APIRouter(prefix="/v1", tags=["model service (OpenAI / Anthropic compatible)"])
Caller = Annotated[ApiKey, Depends(authenticate)]


def _require_chat(caller: ApiKey) -> None:
    if not caller.allows("chat"):
        raise HTTPException(403, "This key does not have the 'chat' permission.")


async def _catalog(request: Request) -> list[dict[str, Any]]:
    """Every name a caller can use: models, then role names pointing at them."""
    settings, llm = request.app.state.settings, request.app.state.llm
    entries = []
    for kind, models in (("chat", settings.chat_models()), ("embed", settings.embedding_models())):
        for m in models:
            entries.append({"id": m, "kind": kind, "capabilities": sorted(await llm.capabilities(m))})
        for name, target in settings.role_names(kind).items():
            entries.append({"id": name, "kind": kind, "alias_of": target})
    return entries


@router.get("/models")
async def list_models(caller: Caller, request: Request) -> dict:
    """Models and role names on offer. Each model lists what it can do (tools, vision…)."""
    _require_chat(caller)
    return {"object": "list", "data": [
        {"object": "model", "type": "model", "display_name": e["id"], "created": 0,
         "created_at": "1970-01-01T00:00:00Z", "owned_by": "ai-platform", **e}
        for e in await _catalog(request)
    ]}


@router.post("/chat/completions")
async def chat_completions(caller: Caller, request: Request, body: dict = Body(...)):
    """OpenAI format. Usage is read from `usage` (and the last stream chunk, if requested)."""
    def default_thinking_off(b: dict) -> None:
        if "reasoning_effort" not in b and "reasoning" not in b:
            b["reasoning_effort"] = "none"

    return await _forward(caller, request, body, "/v1/chat/completions", "chat",
                          default_thinking_off, _openai_usage)


@router.post("/messages")
async def messages(caller: Caller, request: Request, body: dict = Body(...)):
    """Anthropic format — what Claude Code and the Claude Agent SDK send."""
    def default_thinking_off(b: dict) -> None:
        b.setdefault("thinking", {"type": "disabled"})

    return await _forward(caller, request, body, "/v1/messages", "messages",
                          default_thinking_off, _anthropic_usage)


native_router = APIRouter(prefix="/api", tags=["Ollama-compatible"])


@native_router.post("/chat")
async def ollama_chat(caller: Caller, request: Request, body: dict = Body(...)):
    """Ollama's own chat API — what Forgefy's backend and other Ollama clients send.

    Point such a client's Ollama URL at this server and give it a key as a
    bearer token. Streams newline-delimited JSON by default, as Ollama does.
    """
    def default_thinking_off(b: dict) -> None:
        b.setdefault("think", False)

    return await _forward(caller, request, body, "/api/chat", "ollama-chat", default_thinking_off,
                          _ollama_usage, stream_by_default=True, media_type="application/x-ndjson")


@native_router.get("/tags")
async def ollama_tags(caller: Caller, request: Request) -> dict:
    """The models and role names on offer, in Ollama's format."""
    _require_chat(caller)
    return {"models": [{"name": e["id"], "model": e["id"], "details": {k: v for k, v in e.items() if k != "id"}}
                       for e in await _catalog(request)]}


@router.post("/embeddings")
async def embeddings(caller: Caller, request: Request, body: dict = Body(...)):
    """OpenAI-format embeddings: {"model": "embed", "input": "text" or ["text", …]}."""
    return await _forward(caller, request, body, "/v1/embeddings", "embeddings", None,
                          lambda d: ((d.get("usage") or {}).get("prompt_tokens"), None), kind="embed")


@native_router.post("/embed")
async def ollama_embed(caller: Caller, request: Request, body: dict = Body(...)):
    """Ollama-format embeddings: {"model": "embed", "input": "text" or ["text", …]}."""
    return await _forward(caller, request, body, "/api/embed", "embeddings", None,
                          lambda d: (d.get("prompt_eval_count"), None), kind="embed")


@router.post("/messages/count_tokens")
async def count_tokens(caller: Caller, request: Request, body: dict = Body(...)) -> dict:
    """An estimate (about 4 characters per token): the runtime has no exact counter.

    Clients use it to decide when a conversation is getting long, which an
    estimate serves well enough.
    """
    _require_chat(caller)
    text = json.dumps([body.get("system"), body.get("messages"), body.get("tools")], ensure_ascii=False)
    return {"input_tokens": max(1, len(text) // 4)}


# ── Forwarding ───────────────────────────────────────────────────────────────

def _openai_usage(data: dict) -> tuple[int | None, int | None]:
    usage = data.get("usage") or {}
    return usage.get("prompt_tokens"), usage.get("completion_tokens")


def _anthropic_usage(data: dict) -> tuple[int | None, int | None]:
    usage = data.get("usage") or (data.get("message") or {}).get("usage") or {}
    return usage.get("input_tokens"), usage.get("output_tokens")


def _ollama_usage(data: dict) -> tuple[int | None, int | None]:
    return data.get("prompt_eval_count"), data.get("eval_count")


async def _forward(
    caller: ApiKey, request: Request, body: dict, path: str, task: str,
    apply_thinking_default: Callable[[dict], None] | None,
    read_usage: Callable[[dict], tuple[int | None, int | None]],
    *, kind: str = "chat", stream_by_default: bool = False, media_type: str = "text/event-stream",
):
    _require_chat(caller)
    state = request.app.state
    settings = state.settings

    if kind == "chat" and (not isinstance(body.get("messages"), list) or not body["messages"]):
        raise HTTPException(422, "messages must be a non-empty list")
    if kind == "embed" and not body.get("input"):
        raise HTTPException(422, "input must be a string or a non-empty list of strings")
    requested = body.get("model") or "default"
    model = settings.resolve_model(requested, kind)
    if model is None:
        names = settings._offered(kind) + list(settings.role_names(kind))
        what = "embedding model" if kind == "embed" else "model"
        raise HTTPException(
            422, f"{what.capitalize()} '{requested}' is not offered here. Available: {', '.join(names) or 'none'}",
        )
    body["model"] = model
    # Only models that can think get a thinking setting: Ollama rejects one
    # sent to a model without that capability.
    if settings.THINKING == "off" and apply_thinking_default and "thinking" in await state.llm.capabilities(model):
        apply_thinking_default(body)

    record = UsageRecord(
        request_id=request.headers.get("x-request-id") or uuid.uuid4().hex, key_id=caller.id,
        app=caller.app, environment=caller.environment, task=task, model=model, status="ok", latency_ms=0,
    )
    started = time.monotonic()

    async def finish(tokens: tuple[int | None, int | None] = (None, None), status: str = "ok",
                     error: str | None = None) -> None:
        record.prompt_tokens, record.completion_tokens = tokens
        record.status, record.error = status, error
        record.latency_ms = int((time.monotonic() - started) * 1000)
        try:
            await state.store.log_usage(record)
        except Exception:
            logger.exception("could not record usage request_id=%s", record.request_id)
        logger.info("%s app=%s model=%s (asked %s) status=%s latency_ms=%d request_id=%s",
                    task, caller.app, model, requested, status, record.latency_ms, record.request_id)

    try:
        if body.get("stream", stream_by_default):
            return await _stream(state.llm, path, body, finish, read_usage, media_type)
        async with state.llm.slot() as queue_ms:
            record.queue_ms = queue_ms
            data = await state.llm.forward(path, body)
        await finish(read_usage(data))
        return data
    except ModelBusy as exc:
        await finish(status="busy", error="queue timeout")
        raise HTTPException(503, "The model is busy; retry shortly.", headers={"Retry-After": "30"}) from exc
    except ModelError as exc:
        await finish(status="model_error", error=str(exc))
        raise HTTPException(exc.status, f"Model error: {exc}") from exc


async def _stream(llm, path: str, body: dict, finish, read_usage, media_type: str) -> StreamingResponse:
    """Relay the model's stream (SSE or NDJSON). The model slot is held until it ends.

    The first line is read before answering, so a missing model or a full
    queue is reported as a proper HTTP error rather than a broken stream.
    """
    slot = llm.slot()
    await slot.__aenter__()  # raises ModelBusy
    upstream = llm.forward_stream(path, body)
    try:
        first = await upstream.__anext__()
    except BaseException:
        await slot.__aexit__(None, None, None)
        raise

    async def lines() -> AsyncIterator[bytes]:
        yield first
        async for line in upstream:
            yield line

    async def relay() -> AsyncIterator[bytes]:
        prompt_tokens = completion_tokens = None
        status, error = "ok", None
        try:
            async for line in lines():
                # Usage arrives in an SSE event ("data: {...}") or, for Ollama's
                # own API, in the final JSON line.
                payload = line[6:] if line.startswith(b"data: {") else line
                if payload.startswith(b"{") and (b'"usage"' in payload or b'"eval_count"' in payload):
                    try:
                        p, c = read_usage(json.loads(payload))
                        prompt_tokens = p if p is not None else prompt_tokens
                        completion_tokens = c if c is not None else completion_tokens
                    except ValueError:
                        pass
                yield line
        except Exception as exc:  # the client sees the stream end; we record why
            status, error = "model_error", str(exc)
        finally:
            await slot.__aexit__(None, None, None)
            await finish((prompt_tokens, completion_tokens), status, error)

    return StreamingResponse(relay(), media_type=media_type)


def error_body(path: str, status: int, message: str) -> dict[str, Any]:
    """Errors in the shape each client family expects to parse."""
    if path.startswith("/v1/messages"):
        kind = {400: "invalid_request_error", 401: "authentication_error", 403: "permission_error",
                404: "not_found_error", 422: "invalid_request_error", 429: "rate_limit_error",
                503: "overloaded_error"}.get(status, "api_error")
        return {"type": "error", "error": {"type": kind, "message": message}}
    if path.startswith("/v1/"):
        kind = {401: "invalid_api_key", 403: "permission_denied", 429: "rate_limit_exceeded",
                503: "server_overloaded"}.get(status, "invalid_request_error")
        return {"error": {"message": message, "type": kind, "code": status}}
    if path in ("/api/chat", "/api/tags", "/api/embed"):
        return {"error": message}  # Ollama's shape
    return {"detail": message}
