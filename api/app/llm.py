"""Client for the private model runtime (Ollama).

Requests are passed through as the caller sent them. Every call waits its
turn: a CPU server answers fastest one request at a time, so calls queue for
a slot and give up with ModelBusy rather than piling up.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

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


class OllamaClient:
    def __init__(
        self, base_url: str, *, concurrency: int, queue_timeout: float, timeout: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._sem = asyncio.Semaphore(max(1, concurrency))
        self._queue_timeout = queue_timeout
        self._capabilities: dict[str, set[str]] = {}
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

    # Ollama speaks its own /api/chat, OpenAI's /v1/chat/completions and
    # Anthropic's /v1/messages. The caller holds a slot() around these.

    async def forward(self, path: str, body: dict) -> dict:
        """One non-streaming call; returns the JSON reply."""
        try:
            resp = await self._client.post(path, json={**body, "stream": False})
        except httpx.TimeoutException as exc:
            raise ModelError("the model took too long to answer") from exc
        except httpx.HTTPError as exc:
            raise ModelError(f"model runtime unreachable: {exc}") from exc
        self._raise_for_status(resp, body.get("model", ""))
        return resp.json()

    async def forward_stream(self, path: str, body: dict) -> AsyncIterator[bytes]:
        """A streaming call, passed through line by line (SSE or NDJSON)."""
        try:
            async with self._client.stream("POST", path, json={**body, "stream": True}) as resp:
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
            raise ModelError(f"model '{model}' is not downloaded on this server (scripts/pull-models.sh)", 404)
        if resp.is_error:
            status = 400 if 400 <= resp.status_code < 500 else 503
            raise ModelError(f"model runtime returned HTTP {resp.status_code}: {resp.text[:300]}", status)

    async def capabilities(self, model: str) -> set[str]:
        """What a model can do, per Ollama: completion, tools, thinking, vision, embedding…

        Cached: it only changes when a model is re-pulled. Empty if unknown.
        """
        if model not in self._capabilities:
            try:
                resp = await self._client.post("/api/show", json={"model": model}, timeout=10)
                resp.raise_for_status()
                self._capabilities[model] = set(resp.json().get("capabilities") or [])
            except (httpx.HTTPError, ValueError):
                return set()
        return self._capabilities[model]

    async def installed_models(self) -> list[dict]:
        """Models downloaded on the server, with their size on disk (Ollama /api/tags)."""
        resp = await self._client.get("/api/tags", timeout=10)
        resp.raise_for_status()
        return resp.json().get("models", [])

    async def list_models(self) -> list[str]:
        return [m["name"] for m in await self.installed_models()]

    async def loaded_models(self) -> list[dict]:
        """Models in memory now, with their RAM/VRAM footprint (Ollama /api/ps)."""
        resp = await self._client.get("/api/ps", timeout=10)
        resp.raise_for_status()
        return resp.json().get("models", [])
