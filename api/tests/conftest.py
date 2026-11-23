from __future__ import annotations

import copy
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.auth import new_key
from app.config import Settings
from app.llm import ChatResult, ModelBusy
from app.main import create_app
from app.plugins import load_plugins
from app.skills import load_skills
from app.store import MemoryStore

PROJECT = Path(__file__).resolve().parents[2]


class FakeLLM:
    """Stands in for Ollama: returns queued replies and records each call."""

    def __init__(self) -> None:
        self.replies: list[Any] = []
        self.calls: list[dict] = []
        self.models = ["qwen3:8b"]
        # OpenAI-format chat
        self.chat_replies: list[Any] = []
        self.chat_calls: list[dict] = []
        self.stream_lines: list[bytes] = []

    @asynccontextmanager
    async def slot(self):
        yield 0

    async def chat_completions(self, body: dict) -> dict:
        self.chat_calls.append(copy.deepcopy(body))
        item = self.chat_replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return copy.deepcopy(item)

    async def stream_chat_completions(self, body: dict):
        self.chat_calls.append(copy.deepcopy(body))
        for line in self.stream_lines:
            if isinstance(line, Exception):
                raise line
            yield line

    def reply(self, *items: Any) -> None:
        """Queue replies: a dict (sent as JSON), a raw string, or an exception to raise."""
        self.replies.extend(items)

    async def chat_json(self, model, messages, schema, *, num_predict, temperature):
        self.calls.append({"model": model, "messages": messages, "schema": schema})
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        content = item if isinstance(item, str) else json.dumps(item)
        return ChatResult(content=content, queue_ms=5, prompt_tokens=100, completion_tokens=50, generation_ms=2000)

    async def list_models(self):
        return self.models

    async def loaded_models(self):
        return []

    async def close(self):
        pass


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
def settings() -> Settings:
    return Settings(STORE="memory", DEFAULT_MODEL="qwen3:8b", MAX_TRANSCRIPT_CHARS=2000,
                    CHAT_MODELS="qwen3:8b,qwen3:4b", MAX_TOOL_STEPS=4)


@pytest.fixture(scope="session")
def project_skills():
    """The skills and plugins shipped in the project folder."""
    return load_skills(PROJECT / "skills")


@pytest.fixture(scope="session")
def project_plugins():
    return load_plugins(PROJECT / "plugins")


@pytest.fixture
async def client(settings, store, llm, project_skills, project_plugins):
    app = create_app(settings=settings, store=store, llm=llm, skills=project_skills, plugins=project_plugins)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def make_key(
    store: MemoryStore, app: str = "forgefy", rate: int = 30, env: str = "production",
    scopes: list[str] | None = None,
) -> str:
    key, prefix, key_hash = new_key()
    await store.create_key(app=app, environment=env, name="test", prefix=prefix, key_hash=key_hash,
                           rate_limit_per_minute=rate, scopes=scopes)
    return key


def completion(content: str | None = None, tool_calls: list | None = None, finish: str = "stop") -> dict:
    """An OpenAI-format chat completion, as Ollama's /v1 returns it."""
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-1", "object": "chat.completion", "created": 1, "model": "qwen3:8b",
        "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tool_calls else finish}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def tool_call(name: str, args: dict, call_id: str = "call_1") -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


__all__ = ["FakeLLM", "ModelBusy", "bearer", "completion", "make_key", "tool_call"]
