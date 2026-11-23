"""The AI platform API.

    GET  /healthz                                 liveness (no key)
    GET  /api/v1/health                           who you are, runtime and models
    POST /api/v1/agents/{app}/{task}              run an agent, e.g. /agents/forgefy/requirements
    GET  /api/v1/usage?days=7                     your application's usage
    /v1/…                                         general OpenAI-compatible API (app/chat.py)

Every agent call is answered with the same envelope (AgentResponse) and logged
to the usage table — metadata only, never the content.
"""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.agents import AGENTS
from app.agents.base import Agent, InvalidOutput, run_agent
from app.auth import RateLimiter, authenticate
from app.chat import router as chat_router
from app.config import Settings, get_settings
from app.llm import ModelBusy, ModelError, OllamaClient
from app.plugins import Plugin, load_plugins
from app.skills import Skill, load_skills
from app.store import ApiKey, Store, UsageRecord, make_store

logger = logging.getLogger("ai-api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


class Usage(BaseModel):
    queue_ms: int
    prompt_tokens: int
    completion_tokens: int
    tokens_per_second: float | None
    attempts: int


class AgentResponse(BaseModel):
    request_id: str
    agent: str
    agent_version: str
    model: str
    latency_ms: int
    warnings: list[str]
    usage: Usage
    result: dict[str, Any]


Caller = Annotated[ApiKey, Depends(authenticate)]


def create_app(
    settings: Settings | None = None, store: Store | None = None, llm: OllamaClient | None = None,
    skills: dict[str, Skill] | None = None, plugins: dict[str, Plugin] | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await app.state.store.init()
        yield
        await app.state.llm.close()
        await app.state.store.close()

    app = FastAPI(
        title="AI Platform API",
        version="0.1.0",
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
        num_ctx=settings.NUM_CTX,
    )
    app.state.rate_limiter = RateLimiter()
    app.state.skills = skills if skills is not None else load_skills(settings.SKILLS_DIR)
    app.state.plugins = plugins if plugins is not None else load_plugins(settings.PLUGINS_DIR)
    app.include_router(chat_router)

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
        agents = {
            key: {"model": settings.model_for(key), "version": agent.version,
                  "model_downloaded": settings.model_for(key) in installed}
            for key, agent in AGENTS.items() if agent.app == caller.app
        }
        return {
            "app": caller.app, "environment": caller.environment, "key": caller.prefix,
            "scopes": caller.scopes, "runtime": runtime, "models": installed, "agents": agents,
            "chat_models": settings.chat_models() if caller.allows("chat") else [],
            "skills": list(app.state.skills) if caller.allows("skills") else [],
            "plugins": [p for p in app.state.plugins if caller.allows(f"plugin:{p}")],
        }

    @app.get("/api/v1/usage")
    async def usage(caller: Caller, days: int = Query(7, ge=1, le=365)) -> dict[str, Any]:
        return {"app": caller.app, "days": days, "rows": await app.state.store.usage_summary(caller.app, days)}

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error on %s", request.url.path)
        return JSONResponse({"detail": "Internal error."}, status_code=500)

    for agent in AGENTS.values():
        _add_agent_route(app, agent, settings)
    return app


def _add_agent_route(app: FastAPI, agent: Agent, settings: Settings) -> None:
    input_model = agent.input_model

    async def endpoint(body: input_model, caller: Caller, request: Request) -> AgentResponse:  # type: ignore[valid-type]
        if caller.app != agent.app:
            raise HTTPException(403, f"This key belongs to '{caller.app}' and cannot use {agent.app} agents.")
        if not caller.allows("agents"):
            raise HTTPException(403, "This key does not have the 'agents' permission.")

        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        model = settings.model_for(agent.key)
        started = time.monotonic()
        record = UsageRecord(
            request_id=request_id, key_id=caller.id, app=caller.app, environment=caller.environment,
            task=agent.task, agent_version=agent.version, model=model, status="ok", latency_ms=0,
        )
        try:
            agent.check_input(body, settings)
            run = await run_agent(agent, body, request.app.state.llm, model)
        except ValueError as exc:
            record.status, record.error = "bad_request", str(exc)
            raise HTTPException(422, str(exc)) from exc
        except ModelBusy as exc:
            record.status, record.error = "busy", "queue timeout"
            raise HTTPException(503, "The model is busy; retry shortly.", headers={"Retry-After": "30"}) from exc
        except ModelError as exc:
            record.status, record.error = "model_error", str(exc)
            raise HTTPException(503, f"Model unavailable: {exc}") from exc
        except InvalidOutput as exc:
            record.status, record.error = "invalid_output", str(exc)
            raise HTTPException(502, f"The model's answer did not match the schema: {exc}") from exc
        else:
            record.queue_ms, record.prompt_tokens, record.completion_tokens = (
                run.queue_ms, run.prompt_tokens, run.completion_tokens,
            )
        finally:
            record.latency_ms = int((time.monotonic() - started) * 1000)
            try:
                await request.app.state.store.log_usage(record)
            except Exception:
                logger.exception("could not record usage request_id=%s", request_id)
            logger.info(
                "agent=%s app=%s env=%s status=%s latency_ms=%d model=%s request_id=%s",
                agent.key, caller.app, caller.environment, record.status, record.latency_ms, model, request_id,
            )

        return AgentResponse(
            request_id=request_id, agent=agent.key, agent_version=agent.version, model=model,
            latency_ms=record.latency_ms, warnings=run.warnings, result=run.result,
            usage=Usage(
                queue_ms=run.queue_ms, prompt_tokens=run.prompt_tokens, completion_tokens=run.completion_tokens,
                tokens_per_second=run.tokens_per_second, attempts=run.attempts,
            ),
        )

    # Annotations in this module are strings (postponed evaluation); FastAPI
    # resolves them against module globals, where this agent's input model is
    # not. Give it the class itself.
    endpoint.__annotations__["body"] = input_model

    app.add_api_route(
        f"/api/v1/agents/{agent.app}/{agent.task}", endpoint, methods=["POST"],
        response_model=AgentResponse, summary=agent.summary, tags=[agent.app],
        name=agent.key,
    )


app = create_app()
