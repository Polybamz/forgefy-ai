"""General-purpose, OpenAI-compatible API — for anything, not just our apps.

    GET  /v1/models              models this server offers
    POST /v1/chat/completions    chat: system instructions, your own tools, streaming,
                                 JSON mode — plus server-side skills and plugins
    GET  /v1/skills              skills you can enable
    GET  /v1/plugins             plugins your key may use, with their tools

Point any OpenAI client at https://<domain>/v1 with an API key. Two extension
fields on /v1/chat/completions (with the OpenAI SDKs, pass them in extra_body):

    "skills":  ["invoice-writer"] or "all"
    "plugins": ["web", "calculator"]

Without them a request goes straight to the model, streaming included, and any
tools you send come back to you as tool_calls to run yourself. With them, the
server runs its own tools in a loop — loading skills, calling plugins — and
returns the final answer; the steps it took are listed in `x_tool_steps`.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.auth import authenticate
from app.llm import ModelBusy, ModelError
from app.plugins import SEPARATOR, Plugin, call_tool, openai_tools
from app.skills import LOAD_SKILL, Skill, load_skill_tool, read_skill, skills_prompt
from app.store import ApiKey, UsageRecord

logger = logging.getLogger("ai-api")
router = APIRouter(prefix="/v1", tags=["general (OpenAI-compatible)"])
Caller = Annotated[ApiKey, Depends(authenticate)]

Handler = Callable[[Any], Awaitable[str]]


def _require(caller: ApiKey, scope: str) -> None:
    if not caller.allows(scope):
        raise HTTPException(403, f"This key does not have the '{scope}' permission.")


# ── Discovery ────────────────────────────────────────────────────────────────

@router.get("/models")
async def list_models(caller: Caller, request: Request) -> dict:
    _require(caller, "chat")
    models = request.app.state.settings.chat_models()
    return {"object": "list", "data": [
        {"id": m, "object": "model", "created": 0, "owned_by": "ai-platform"} for m in models
    ]}


@router.get("/skills")
async def list_skills(caller: Caller, request: Request) -> dict:
    _require(caller, "skills")
    return {"skills": [
        {"name": s.name, "description": s.description, "files": s.files}
        for s in request.app.state.skills.values()
    ]}


@router.get("/plugins")
async def list_plugins(caller: Caller, request: Request) -> dict:
    return {"plugins": [
        {"name": p.name, "description": p.description,
         "tools": [t["function"] for t in openai_tools(p)]}
        for p in request.app.state.plugins.values() if caller.allows(f"plugin:{p.name}")
    ]}


# ── Chat ─────────────────────────────────────────────────────────────────────

@router.post("/chat/completions")
async def chat_completions(caller: Caller, request: Request, body: dict = Body(...)):
    _require(caller, "chat")
    state = request.app.state
    settings = state.settings

    if not isinstance(body.get("messages"), list) or not body["messages"]:
        raise HTTPException(422, "messages must be a non-empty list")
    model = body.get("model") or settings.DEFAULT_MODEL
    if model == "default":
        model = settings.DEFAULT_MODEL
    allowed = settings.chat_models()
    if model not in allowed:
        raise HTTPException(422, f"Model '{model}' is not offered here. Available: {', '.join(allowed)}")
    body["model"] = model

    skills = _requested_skills(caller, state.skills, body.pop("skills", None))
    plugins = _requested_plugins(caller, state.plugins, body.pop("plugins", None))

    record = UsageRecord(
        request_id=request.headers.get("x-request-id") or uuid.uuid4().hex, key_id=caller.id,
        app=caller.app, environment=caller.environment, task="chat", model=model, status="ok", latency_ms=0,
    )
    started = time.monotonic()

    async def finish(usage: dict | None = None, status: str = "ok", error: str | None = None) -> None:
        record.status, record.error = status, error
        if usage:
            record.prompt_tokens = usage.get("prompt_tokens")
            record.completion_tokens = usage.get("completion_tokens")
        record.latency_ms = int((time.monotonic() - started) * 1000)
        try:
            await state.store.log_usage(record)
        except Exception:
            logger.exception("could not record usage request_id=%s", record.request_id)
        logger.info("chat app=%s model=%s status=%s latency_ms=%d skills=%d plugins=%d request_id=%s",
                    caller.app, model, status, record.latency_ms, len(skills), len(plugins), record.request_id)

    try:
        if not skills and not plugins:
            if body.get("stream"):
                return await _stream_passthrough(state.llm, body, finish)
            async with state.llm.slot() as queue_ms:
                record.queue_ms = queue_ms
                data = await state.llm.chat_completions(body)
            await finish(data.get("usage"))
            return data

        data = await _run_with_server_tools(state, body, skills, plugins, settings)
        await finish(data.get("usage"))
        if body.get("stream"):
            include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
            return StreamingResponse(_as_sse(data, include_usage), media_type="text/event-stream")
        return data
    except ModelBusy as exc:
        await finish(status="busy", error="queue timeout")
        raise HTTPException(503, "The model is busy; retry shortly.", headers={"Retry-After": "30"}) from exc
    except ModelError as exc:
        await finish(status="model_error", error=str(exc))
        raise HTTPException(exc.status, f"Model error: {exc}") from exc


def _requested_skills(caller: ApiKey, available: dict[str, Skill], requested: Any) -> list[Skill]:
    if not requested:
        return []
    _require(caller, "skills")
    if requested == "all":
        return list(available.values())
    if not isinstance(requested, list) or not all(isinstance(s, str) for s in requested):
        raise HTTPException(422, 'skills must be a list of skill names, or "all"')
    unknown = [s for s in requested if s not in available]
    if unknown:
        raise HTTPException(422, f"Unknown skills: {', '.join(unknown)}. Available: {', '.join(available) or 'none'}")
    return [available[s] for s in dict.fromkeys(requested)]


def _requested_plugins(caller: ApiKey, available: dict[str, Plugin], requested: Any) -> list[Plugin]:
    if not requested:
        return []
    if not isinstance(requested, list) or not all(isinstance(p, str) for p in requested):
        raise HTTPException(422, "plugins must be a list of plugin names")
    unknown = [p for p in requested if p not in available]
    if unknown:
        raise HTTPException(422, f"Unknown plugins: {', '.join(unknown)}. Available: {', '.join(available) or 'none'}")
    for name in requested:
        _require(caller, f"plugin:{name}")
    return [available[p] for p in dict.fromkeys(requested)]


async def _stream_passthrough(llm, body: dict, finish) -> StreamingResponse:
    """Stream straight from the model. The slot is held until the stream ends.

    The first line is read before answering, so a missing model or a busy
    server is still reported as a proper HTTP error rather than a broken stream.
    """
    slot = llm.slot()
    await slot.__aenter__()  # raises ModelBusy
    upstream = llm.stream_chat_completions(body)
    try:
        first = await upstream.__anext__()
    except BaseException:
        await slot.__aexit__(None, None, None)
        raise

    async def relay() -> AsyncIterator[bytes]:
        usage = None
        status, error = "ok", None
        try:
            yield first
            async for line in upstream:
                if line.startswith(b"data: {") and b'"usage"' in line:
                    try:
                        usage = json.loads(line[6:]).get("usage") or usage
                    except ValueError:
                        pass
                yield line
        except Exception as exc:  # the client sees the stream end; we record why
            status, error = "model_error", str(exc)
        finally:
            await slot.__aexit__(None, None, None)
            await finish(usage, status, error)

    return StreamingResponse(relay(), media_type="text/event-stream")


async def _run_with_server_tools(state, body: dict, skills: list[Skill], plugins: list[Plugin], settings) -> dict:
    """Call the model, run the server-side tools it asks for, repeat until it answers.

    The caller's own tools stay theirs: if the model calls one, the loop stops
    and that call is returned for the caller to run. (If one turn mixes the
    caller's tools with server tools, only the caller's calls are returned.)
    """
    handlers: dict[str, Handler] = {}
    server_tools: list[dict] = []
    if skills:
        by_name = {s.name: s for s in skills}

        async def load(arguments: Any) -> str:
            try:
                args = json.loads(arguments or "{}") if isinstance(arguments, str) else arguments
            except ValueError:
                return "error: arguments must be JSON"
            return read_skill(by_name, str(args.get("name", "")), args.get("file"))

        handlers[LOAD_SKILL] = load
        server_tools.append(load_skill_tool(skills))
    for plugin in plugins:
        for tool_def in openai_tools(plugin):
            full_name = tool_def["function"]["name"]
            tool_name = full_name.split(SEPARATOR, 1)[1]

            async def run(arguments: Any, plugin=plugin, tool_name=tool_name) -> str:
                return await call_tool(plugin, tool_name, arguments, settings.TOOL_TIMEOUT_SECONDS)

            handlers[full_name] = run
            server_tools.append(tool_def)

    client_tools = list(body.get("tools") or [])
    clashes = {t.get("function", {}).get("name") for t in client_tools} & set(handlers)
    if clashes:
        raise HTTPException(422, f"Your tool names clash with server tools: {', '.join(sorted(clashes))}")

    messages = list(body["messages"])
    if skills:
        messages = _with_system(messages, skills_prompt(skills))

    request = {k: v for k, v in body.items() if k not in ("stream", "stream_options")}
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    steps: list[dict] = []
    data: dict = {}

    for step in range(settings.MAX_TOOL_STEPS):
        call = {**request, "messages": messages, "tools": client_tools + server_tools}
        if step == settings.MAX_TOOL_STEPS - 1:
            call["tool_choice"] = "none"  # last round: answer with what you have
        async with state.llm.slot():
            data = await state.llm.chat_completions(call)
        for k in usage:
            usage[k] += (data.get("usage") or {}).get(k) or 0

        choice = data["choices"][0]
        message = choice["message"]
        calls = message.get("tool_calls") or []
        server_calls = [c for c in calls if c.get("function", {}).get("name") in handlers]
        client_calls = [c for c in calls if c not in server_calls]
        if not server_calls:
            break
        if client_calls:
            message["tool_calls"] = client_calls
            choice["finish_reason"] = "tool_calls"
            break
        if step == settings.MAX_TOOL_STEPS - 1:
            # The model ignored tool_choice=none on the last round.
            message["tool_calls"] = None
            message["content"] = message.get("content") or "Stopped: too many tool steps without an answer."
            choice["finish_reason"] = "length"
            break

        messages.append(message)
        for c in server_calls:
            name = c["function"]["name"]
            t0 = time.monotonic()
            result = await handlers[name](c["function"].get("arguments"))
            steps.append({"tool": name, "arguments": c["function"].get("arguments"),
                          "ok": not result.startswith("error:"),
                          "ms": int((time.monotonic() - t0) * 1000)})
            messages.append({"role": "tool", "tool_call_id": c.get("id"), "content": result})

    data["usage"] = usage
    data["x_tool_steps"] = steps
    return data


def _with_system(messages: list[dict], extra: str) -> list[dict]:
    """Add text to the caller's system message, or put one first."""
    first = messages[0] if messages else {}
    if first.get("role") == "system" and isinstance(first.get("content"), str):
        return [{**first, "content": f"{first['content']}\n\n{extra}"}, *messages[1:]]
    return [{"role": "system", "content": extra}, *messages]


async def _as_sse(data: dict, include_usage: bool) -> AsyncIterator[bytes]:
    """A finished completion as an OpenAI-style event stream (for stream=true with server tools)."""
    base = {"id": data.get("id"), "object": "chat.completion.chunk", "created": data.get("created"),
            "model": data.get("model")}
    choice = data["choices"][0]
    message = choice["message"]
    delta: dict[str, Any] = {"role": "assistant", "content": message.get("content") or ""}
    if message.get("tool_calls"):
        delta["tool_calls"] = [{"index": i, **c} for i, c in enumerate(message["tool_calls"])]

    def event(payload: dict) -> bytes:
        return f"data: {json.dumps({**base, **payload}, ensure_ascii=False)}\n\n".encode()

    yield event({"choices": [{"index": 0, "delta": delta, "finish_reason": None}]})
    yield event({"choices": [{"index": 0, "delta": {}, "finish_reason": choice.get("finish_reason")}],
                 "x_tool_steps": data.get("x_tool_steps", [])})
    if include_usage:
        yield event({"choices": [], "usage": data.get("usage")})
    yield b"data: [DONE]\n\n"
