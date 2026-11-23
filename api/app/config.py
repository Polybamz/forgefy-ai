"""Settings, read from the environment (docker-compose passes them from .env)."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # "postgres" in production; "memory" for tests and quick local runs.
    STORE: str = "postgres"
    DATABASE_URL: str = "postgresql://ai:ai@postgres:5432/ai"

    OLLAMA_URL: str = "http://ollama:11434"
    # The model every agent uses unless MODEL_OVERRIDES names another, as
    # "agent=model" pairs: "vndor.product-description=qwen3:4b,forgefy.requirements=qwen3:8b"
    DEFAULT_MODEL: str = "qwen3:8b"
    MODEL_OVERRIDES: str = ""

    # Model calls running at once. On a CPU server one at a time is fastest
    # overall; more only splits the same cores. Match OLLAMA_NUM_PARALLEL.
    MODEL_CONCURRENCY: int = 1
    # How long a request may wait for its turn before getting 503 + Retry-After.
    QUEUE_TIMEOUT_SECONDS: float = 120.0
    MODEL_TIMEOUT_SECONDS: float = 600.0
    # Context window per call. A 20k-character transcript is ~6k tokens, plus
    # instructions and the answer; more context costs RAM.
    NUM_CTX: int = 12288

    DEFAULT_RATE_LIMIT_PER_MINUTE: int = 30
    MAX_TRANSCRIPT_CHARS: int = 20_000
    DOCS_ENABLED: bool = True

    # ── General chat API (/v1, OpenAI-compatible) ────────────────────────────
    # Models callers may ask for, comma-separated; blank = DEFAULT_MODEL and
    # any MODEL_OVERRIDES models. An allowlist, because each model a caller
    # names must be loaded into memory — on a small server, swapping models
    # per request would make every call slow.
    CHAT_MODELS: str = ""
    SKILLS_DIR: str = "/srv/skills"
    PLUGINS_DIR: str = "/srv/plugins"
    # Rounds of server-side tool calls (skills, plugins) per request.
    MAX_TOOL_STEPS: int = 8
    TOOL_TIMEOUT_SECONDS: float = 30.0

    def chat_models(self) -> list[str]:
        listed = [m.strip() for m in self.CHAT_MODELS.split(",") if m.strip()]
        if listed:
            return listed
        overrides = [item.partition("=")[2].strip() for item in self.MODEL_OVERRIDES.split(",")]
        return list(dict.fromkeys([self.DEFAULT_MODEL, *(m for m in overrides if m)]))

    def model_for(self, agent_key: str) -> str:
        for item in self.MODEL_OVERRIDES.split(","):
            name, sep, model = item.partition("=")
            if sep and name.strip() == agent_key and model.strip():
                return model.strip()
        return self.DEFAULT_MODEL


@lru_cache
def get_settings() -> Settings:
    return Settings()
