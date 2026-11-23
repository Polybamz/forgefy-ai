"""Persistent records: API keys (hashed) and the usage log.

The usage log holds metadata only — which app, which task, model, status,
latency, token counts. Prompts, transcripts and outputs are never stored.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# Applications with their own agents. Keys can also be made for any other
# name (a script, n8n, an editor…) to use the general chat API.
APPS = ("forgefy", "vndor")
ENVIRONMENTS = ("production", "staging", "development")
# What a key may use: its app's agents, the OpenAI-compatible chat API, skills,
# and plugins — each plugin granted by name ("plugin:web") or all ("plugin:*").
DEFAULT_SCOPES = ("agents", "chat", "skills")


@dataclass
class ApiKey:
    id: int
    app: str
    environment: str
    name: str
    prefix: str
    rate_limit_per_minute: int
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    scopes: list[str] = field(default_factory=lambda: list(DEFAULT_SCOPES))

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None

    def allows(self, scope: str) -> bool:
        if scope in self.scopes:
            return True
        return scope.startswith("plugin:") and "plugin:*" in self.scopes


@dataclass
class UsageRecord:
    request_id: str
    key_id: int | None
    app: str
    environment: str
    task: str
    status: str  # ok | invalid_output | model_error | busy | bad_request
    latency_ms: int
    agent_version: str | None = None
    model: str | None = None
    queue_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class Store(Protocol):
    async def init(self) -> None: ...
    async def close(self) -> None: ...
    async def create_key(
        self, *, app: str, environment: str, name: str, prefix: str, key_hash: str,
        rate_limit_per_minute: int, scopes: list[str] | None = None,
    ) -> ApiKey: ...
    async def find_key(self, key_hash: str) -> ApiKey | None: ...
    async def list_keys(self) -> list[ApiKey]: ...
    async def revoke_key(self, prefix: str) -> bool: ...
    async def set_scopes(self, prefix: str, scopes: list[str]) -> bool: ...
    async def touch_key(self, key_id: int) -> None: ...
    async def log_usage(self, record: UsageRecord) -> None: ...
    async def usage_summary(self, app: str | None, days: int) -> list[dict[str, Any]]: ...


def _summarize(records: list[UsageRecord]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[UsageRecord]] = defaultdict(list)
    for r in records:
        groups[(r.app, r.task, r.status)].append(r)
    rows = []
    for (app, task, status), items in sorted(groups.items()):
        latencies = sorted(i.latency_ms for i in items)
        rows.append({
            "app": app,
            "task": task,
            "status": status,
            "requests": len(items),
            "avg_latency_ms": round(sum(latencies) / len(latencies)),
            "p95_latency_ms": latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))],
            "prompt_tokens": sum(i.prompt_tokens or 0 for i in items),
            "completion_tokens": sum(i.completion_tokens or 0 for i in items),
        })
    return rows


class MemoryStore:
    """In-process store for tests and throwaway local runs. Lost on restart."""

    def __init__(self) -> None:
        self._keys: dict[str, tuple[str, ApiKey]] = {}  # prefix -> (hash, key)
        self.usage: list[UsageRecord] = []

    async def init(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def create_key(self, *, app, environment, name, prefix, key_hash, rate_limit_per_minute, scopes=None):
        key = ApiKey(
            id=len(self._keys) + 1, app=app, environment=environment, name=name,
            prefix=prefix, rate_limit_per_minute=rate_limit_per_minute,
            created_at=datetime.now(UTC), scopes=list(scopes or DEFAULT_SCOPES),
        )
        self._keys[prefix] = (key_hash, key)
        return key

    async def find_key(self, key_hash):
        return next((k for h, k in self._keys.values() if h == key_hash), None)

    async def list_keys(self):
        return [k for _, k in self._keys.values()]

    async def revoke_key(self, prefix):
        entry = self._keys.get(prefix)
        if not entry or entry[1].revoked:
            return False
        entry[1].revoked_at = datetime.now(UTC)
        return True

    async def set_scopes(self, prefix, scopes):
        entry = self._keys.get(prefix)
        if not entry:
            return False
        entry[1].scopes = list(scopes)
        return True

    async def touch_key(self, key_id):
        for _, key in self._keys.values():
            if key.id == key_id:
                key.last_used_at = datetime.now(UTC)

    async def log_usage(self, record):
        self.usage.append(record)

    async def usage_summary(self, app, days):
        since = datetime.now(UTC) - timedelta(days=days)
        return _summarize([r for r in self.usage if r.created_at >= since and (app is None or r.app == app)])


_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    id BIGSERIAL PRIMARY KEY,
    app TEXT NOT NULL,
    environment TEXT NOT NULL,
    name TEXT NOT NULL,
    prefix TEXT NOT NULL UNIQUE,
    key_hash TEXT NOT NULL UNIQUE,
    rate_limit_per_minute INT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS usage_log (
    id BIGSERIAL PRIMARY KEY,
    request_id TEXT NOT NULL,
    key_id BIGINT REFERENCES api_keys(id),
    app TEXT NOT NULL,
    environment TEXT NOT NULL,
    task TEXT NOT NULL,
    agent_version TEXT,
    model TEXT,
    status TEXT NOT NULL,
    latency_ms INT NOT NULL,
    queue_ms INT,
    prompt_tokens INT,
    completion_tokens INT,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS usage_log_app_created ON usage_log (app, created_at);
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS scopes TEXT[] NOT NULL DEFAULT '{agents,chat,skills}';
"""

_KEY_COLUMNS = (
    "id, app, environment, name, prefix, rate_limit_per_minute, created_at, last_used_at, revoked_at, scopes"
)


class PostgresStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool = None

    async def init(self) -> None:
        import asyncpg

        # Postgres may still be starting when the API container comes up.
        for attempt in range(30):
            try:
                self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
                break
            except (OSError, asyncpg.PostgresError) as exc:
                if attempt == 29:
                    raise
                logger.info("waiting for postgres: %s", exc)
                await asyncio.sleep(2)
        async with self._pool.acquire() as conn:
            await conn.execute(_SCHEMA)

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()

    @staticmethod
    def _key(row) -> ApiKey:
        data = dict(row)
        data["scopes"] = list(data.get("scopes") or [])
        return ApiKey(**data)

    async def set_scopes(self, prefix: str, scopes: list[str]) -> bool:
        result = await self._pool.execute("UPDATE api_keys SET scopes = $2 WHERE prefix = $1", prefix, scopes)
        return result.endswith(" 1")

    async def create_key(self, *, app, environment, name, prefix, key_hash, rate_limit_per_minute, scopes=None):
        row = await self._pool.fetchrow(
            f"INSERT INTO api_keys (app, environment, name, prefix, key_hash, rate_limit_per_minute, scopes) "
            f"VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING {_KEY_COLUMNS}",
            app, environment, name, prefix, key_hash, rate_limit_per_minute, list(scopes or DEFAULT_SCOPES),
        )
        return self._key(row)

    async def find_key(self, key_hash):
        row = await self._pool.fetchrow(f"SELECT {_KEY_COLUMNS} FROM api_keys WHERE key_hash = $1", key_hash)
        return self._key(row) if row else None

    async def list_keys(self):
        rows = await self._pool.fetch(f"SELECT {_KEY_COLUMNS} FROM api_keys ORDER BY id")
        return [self._key(r) for r in rows]

    async def revoke_key(self, prefix):
        result = await self._pool.execute(
            "UPDATE api_keys SET revoked_at = now() WHERE prefix = $1 AND revoked_at IS NULL", prefix,
        )
        return result.endswith(" 1")

    async def touch_key(self, key_id):
        await self._pool.execute("UPDATE api_keys SET last_used_at = now() WHERE id = $1", key_id)

    async def log_usage(self, record):
        await self._pool.execute(
            "INSERT INTO usage_log (request_id, key_id, app, environment, task, agent_version, model, "
            "status, latency_ms, queue_ms, prompt_tokens, completion_tokens, error, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)",
            record.request_id, record.key_id, record.app, record.environment, record.task,
            record.agent_version, record.model, record.status, record.latency_ms, record.queue_ms,
            record.prompt_tokens, record.completion_tokens, record.error, record.created_at,
        )

    async def usage_summary(self, app, days):
        rows = await self._pool.fetch(
            """
            SELECT app, task, status, count(*) AS requests,
                   round(avg(latency_ms))::int AS avg_latency_ms,
                   percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms,
                   coalesce(sum(prompt_tokens), 0)::int AS prompt_tokens,
                   coalesce(sum(completion_tokens), 0)::int AS completion_tokens
            FROM usage_log
            WHERE created_at >= now() - make_interval(days => $1) AND ($2::text IS NULL OR app = $2)
            GROUP BY app, task, status ORDER BY app, task, status
            """,
            days, app,
        )
        return [dict(r) for r in rows]


def make_store(settings) -> Store:
    if settings.STORE == "memory":
        return MemoryStore()
    return PostgresStore(settings.DATABASE_URL)
