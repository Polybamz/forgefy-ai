"""The model server API: your self-hosted models behind API keys.

    GET  /healthz                      liveness (no key)
    GET  /api/v1/health                who the key belongs to, runtime status, models
    GET  /api/v1/usage?days=7          the key's application's usage
    /v1/…                              OpenAI- and Anthropic-compatible APIs (app/chat.py)
    /api/chat, /api/tags               Ollama's own API (app/chat.py)
    /v1/audio/…                        speech-to-text, OpenAI-compatible (app/speech.py)
    /admin                             the admin console (app/static/admin, API in app/admin.py)

Every model call is logged to the usage table — metadata only, never the content.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.admin import router as admin_router
from app.auth import RateLimiter, authenticate
from app.chat import error_body, native_router
from app.chat import router as chat_router
from app.config import Settings, get_settings
from app.llm import OllamaClient
from app.speech import SpeechClient
from app.speech import router as speech_router
from app.store import ApiKey, Store, make_store

logger = logging.getLogger("ai-api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

Caller = Annotated[ApiKey, Depends(authenticate)]
_STATIC = Path(__file__).parent / "static"


def create_app(
    settings: Settings | None = None, store: Store | None = None, llm: OllamaClient | None = None,
    speech: SpeechClient | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await app.state.store.init()
        yield
        await app.state.llm.close()
        await app.state.speech.close()
        await app.state.store.close()

    app = FastAPI(
        title="Model server",
        version="0.2.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.DOCS_ENABLED else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.DOCS_ENABLED else None,
    )
    app.state.settings = settings
    app.state.store = store or make_store(settings)
    app.state.llm = llm or OllamaClient(
        settings.OLLAMA_URL,
        concurrency=settings.MODEL_CONCURRENCY,
        queue_timeout=settings.QUEUE_TIMEOUT_SECONDS,
        timeout=settings.MODEL_TIMEOUT_SECONDS,
    )
    app.state.speech = speech or SpeechClient(
        settings.SPEECH_URL,
        timeout=settings.SPEECH_TIMEOUT_SECONDS,
        queue_timeout=settings.SPEECH_QUEUE_TIMEOUT_SECONDS,
    )
    app.state.rate_limiter = RateLimiter()
    app.include_router(chat_router)
    app.include_router(native_router)
    app.include_router(speech_router)
    app.include_router(admin_router)

    # The admin console: static files, no build step. Its API is admin_router.
    @app.get("/admin", include_in_schema=False)
    async def admin_redirect() -> RedirectResponse:
        return RedirectResponse("/admin/")

    app.mount("/admin", StaticFiles(directory=_STATIC / "admin", html=True), name="admin")

    @app.middleware("http")
    async def admin_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/admin"):
            # Everything the console loads comes from this server; nothing inline.
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' data:; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
            )
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/health")
    async def health(caller: Caller) -> dict[str, Any]:
        try:
            installed = await app.state.llm.list_models()
            runtime = "ok"
        except Exception as exc:  # report, don't fail: this endpoint is for diagnosis
            installed, runtime = [], f"unreachable: {exc}"
        offered = settings.chat_models() + settings.embedding_models()
        # Ollama lists "mistral" as "mistral:latest".
        have = set(installed) | {m.removesuffix(":latest") for m in installed}
        return {
            "app": caller.app, "environment": caller.environment, "key": caller.prefix,
            "runtime": runtime, "models_offered": offered,
            "roles": {**settings.role_names("chat"), **settings.role_names("embed")},
            "speech": await app.state.speech.health(),
            "models_missing": [m for m in offered if m not in have] if runtime == "ok" else [],
        }

    @app.get("/api/v1/usage")
    async def usage(caller: Caller, days: int = Query(7, ge=1, le=365)) -> dict[str, Any]:
        return {"app": caller.app, "days": days, "rows": await app.state.store.usage_summary(caller.app, days)}

    # Errors in the shape each client family parses: OpenAI's under /v1,
    # Anthropic's under /v1/messages, Ollama's under /api/chat and /api/tags.
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(error_body(request.url.path, exc.status_code, str(exc.detail)),
                            status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        message = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:5])
        return JSONResponse(error_body(request.url.path, 422, message), status_code=422)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error on %s", request.url.path)
        return JSONResponse(error_body(request.url.path, 500, "Internal error."), status_code=500)

    return app


app = create_app()
