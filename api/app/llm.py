"""Client for the private model runtime (Ollama).

Every call asks for JSON constrained to the agent's schema (Ollama's `format`),
and waits its turn: a CPU server answers fastest one request at a time, so
calls queue for a slot and give up with ModelBusy rather than piling up.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx


class ModelBusy(Exception):
    """No slot freed up within the queue timeout."""


class ModelError(Exception):
    """The runtime is unreachable, the model is missing, or the call failed.

    `status` is the HTTP status to report: 400 when the runtime rejected the
    request itself (e.g. tools sent to a model without tool support), else 503.
    """

    def __init__(self, message: str, status: int = 503) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class ChatResult:
    content: str
    queue_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    generation_ms: int | None


def inline_schema(schema: dict) -> dict:
    """Resolve $ref/$defs, which Pydantic emits for nested models, into one tree."""
    defs = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].rsplit("/", 1)[-1]])
            return {k: resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(n) for n in node]
        return node

    return resolve(schema)


class OllamaClient:
    def __init__(
        self, base_url: str, *, concurrency: int, queue_timeout: float, timeout: float, num_ctx: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._sem = asyncio.Semaphore(max(1, concurrency))
        self._queue_timeout = queue_timeout
        self._num_ctx = num_ctx
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=httpx.Timeout(timeout, connect=10), transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[int]:
        """Wait for a free model slot; yields how long the wait took (ms)."""
        waited = time.monotonic()
        try:
            await asyncio.wait_for(self._sem.acquire(), self._queue_timeout)
        except TimeoutError as exc:
            raise ModelBusy from exc
        try:
            yield int((time.monotonic() - waited) * 1000)
        finally:
            self._sem.release()

    # ── OpenAI-compatible chat (Ollama's /v1 API) ────────────────────────────
    # The caller holds a slot() around these.

    async def chat_completions(self, body: dict) -> dict:
        """One non-streaming /v1/chat/completions call; returns the OpenAI-format reply."""
        try:
            resp = await self._client.post("/v1/chat/completions", json={**body, "stream": False})
        except httpx.TimeoutException as exc:
            raise ModelError("the model took too long to answer") from exc
        except httpx.HTTPError as exc:
            raise ModelError(f"model runtime unreachable: {exc}") from exc
        self._raise_for_status(resp, body.get("model", ""))
        return resp.json()

    async def stream_chat_completions(self, body: dict) -> AsyncIterator[bytes]:
        """Server-sent events from /v1/chat/completions, passed through line by line."""
        try:
            async with self._client.stream("POST", "/v1/chat/completions", json={**body, "stream": True}) as resp:
                if resp.is_error:
                    await resp.aread()
                    self._raise_for_status(resp, body.get("model", ""))
                async for line in resp.aiter_lines():
                    yield (line + "\n").encode()
        except httpx.TimeoutException as exc:
            raise ModelError("the model took too long to answer") from exc
        except httpx.HTTPError as exc:
            raise ModelError(f"model runtime unreachable: {exc}") from exc

    @staticmethod
    def _raise_for_status(resp: httpx.Response, model: str) -> None:
        if resp.status_code == 404:
            raise ModelError(f"model '{model}' is not downloaded on this server (scripts/pull-models.sh)")
        if resp.is_error:
            status = 400 if 400 <= resp.status_code < 500 else 503
            raise ModelError(f"model runtime returned HTTP {resp.status_code}: {resp.text[:300]}", status)

    # ── Native API, schema-constrained (used by the agents) ──────────────────

    async def chat_json(
        self, model: str, messages: list[dict], schema: dict, *, num_predict: int, temperature: float,
    ) -> ChatResult:
        waited = time.monotonic()
        try:
            await asyncio.wait_for(self._sem.acquire(), self._queue_timeout)
        except TimeoutError as exc:
            raise ModelBusy from exc
        queue_ms = int((time.monotonic() - waited) * 1000)
        try:
            resp = await self._client.post("/api/chat", json={
                "model": model,
                "messages": messages,
                "stream": False,
                "format": schema,
                # Qwen3 and similar "think" before answering by default; for
                # schema-bound extraction that only costs time.
                "think": False,
                "options": {"num_ctx": self._num_ctx, "num_predict": num_predict, "temperature": temperature},
            })
        except httpx.TimeoutException as exc:
            raise ModelError("the model took too long to answer") from exc
        except httpx.HTTPError as exc:
            raise ModelError(f"model runtime unreachable: {exc}") from exc
        finally:
            self._sem.release()

        if resp.status_code == 404:
            raise ModelError(f"model '{model}' is not downloaded on this server (scripts/pull-models.sh)")
        if resp.is_error:
            raise ModelError(f"model runtime returned HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        eval_ns = data.get("eval_duration")
        return ChatResult(
            content=(data.get("message") or {}).get("content", ""),
            queue_ms=queue_ms,
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
            generation_ms=int(eval_ns / 1e6) if eval_ns else None,
        )

    async def list_models(self) -> list[str]:
        resp = await self._client.get("/api/tags", timeout=10)
        resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", [])]

    async def loaded_models(self) -> list[dict]:
        """Models in memory now, with their RAM/VRAM footprint (Ollama /api/ps)."""
        resp = await self._client.get("/api/ps", timeout=10)
        resp.raise_for_status()
        return resp.json().get("models", [])
