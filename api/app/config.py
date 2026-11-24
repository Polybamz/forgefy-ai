"""Settings, read from the environment (docker-compose passes them from .env)."""
from __future__ import annotations

from fnmatch import fnmatchcase
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # "postgres" in production; "memory" for tests and quick local runs.
    STORE: str = "postgres"
    DATABASE_URL: str = "postgresql://ai:ai@postgres:5432/ai"

    OLLAMA_URL: str = "http://ollama:11434"
    # Used when a request names no model, or asks for "default".
    DEFAULT_MODEL: str = "qwen3:8b"
    # Chat models callers may ask for, comma-separated; blank = DEFAULT_MODEL
    # only. An allowlist: each one must be pulled, and only a couple fit in
    # memory at once on a small server.
    CHAT_MODELS: str = ""
    # Embedding models (text → vectors, for search and similarity), comma-
    # separated; blank = no embeddings endpoint. The first is the default.
    EMBEDDING_MODELS: str = ""
    # Other names that run on one of the models above, as name=model pairs.
    # Role names let apps ask for a kind of model while you choose which
    # model fills the role, and change it later without touching any app:
    #   general=qwen3:8b,fast=qwen3:4b,embed=bge-m3
    # "*" matches anything — Claude Code asks for claude-* models:
    #   claude-*=qwen3:8b
    MODEL_ALIASES: str = ""

    # Model calls running at once. On a CPU server one at a time is fastest
    # overall; more only splits the same cores. Match OLLAMA_NUM_PARALLEL.
    MODEL_CONCURRENCY: int = 1
    # How long a request may wait for its turn before getting 503 + Retry-After.
    QUEUE_TIMEOUT_SECONDS: float = 120.0
    MODEL_TIMEOUT_SECONDS: float = 600.0

    # "model": each model's default (Qwen3 thinks first). "off": answer without
    # a thinking phase unless the request asks for one — about twice as fast
    # on a CPU, but small models then call tools far less reliably and get
    # more facts wrong, so keep "model" when clients use tools.
    THINKING: str = "model"

    DEFAULT_RATE_LIMIT_PER_MINUTE: int = 30
    DOCS_ENABLED: bool = True
    # Password for the admin console at /admin (keys, usage, models,
    # playground). Blank = the console's API is switched off. setup.sh
    # generates a long random one.
    ADMIN_TOKEN: str = ""

    def chat_models(self) -> list[str]:
        listed = [m.strip() for m in self.CHAT_MODELS.split(",") if m.strip()]
        return listed or [self.DEFAULT_MODEL]

    def embedding_models(self) -> list[str]:
        return [m.strip() for m in self.EMBEDDING_MODELS.split(",") if m.strip()]

    def _offered(self, kind: str) -> list[str]:
        return self.embedding_models() if kind == "embed" else self.chat_models()

    def _aliases(self) -> list[tuple[str, str]]:
        pairs = []
        for item in self.MODEL_ALIASES.split(","):
            pattern, sep, target = item.rpartition("=")
            if sep and pattern.strip() and target.strip():
                pairs.append((pattern.strip(), target.strip()))
        return pairs

    def resolve_model(self, requested: str, kind: str = "chat") -> str | None:
        """The model to run for a requested name, or None if it isn't offered.

        kind is "chat" or "embed": a chat request can't run on an embedding
        model, or the reverse.
        """
        offered = self._offered(kind)
        if requested == "default":
            return offered[0] if kind == "embed" and offered else (self.DEFAULT_MODEL if kind == "chat" else None)
        if requested in offered:
            return requested
        for pattern, target in self._aliases():
            if fnmatchcase(requested, pattern) and target in offered:
                return target
        return None

    def role_names(self, kind: str = "chat") -> dict[str, str]:
        """Exact (non-wildcard) aliases whose model is offered, e.g. {"fast": "qwen3:4b"}."""
        offered = self._offered(kind)
        return {name: target for name, target in self._aliases()
                if not any(c in name for c in "*?[") and target in offered}


@lru_cache
def get_settings() -> Settings:
    return Settings()
