"""API keys and per-key rate limits.

A key belongs to one application (forgefy, vndor, …) and one environment, and
may only call that application's agents. Only a SHA-256 hash of each key is
stored; the key itself is shown once, when it is created.
"""
from __future__ import annotations

import hashlib
import secrets
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from app.store import ApiKey

KEY_PREFIX = "aik_"


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_key() -> tuple[str, str, str]:
    """(key, display prefix, hash). The prefix identifies the key in lists and logs."""
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    return key, key[:12], hash_key(key)


class RateLimiter:
    """Sliding one-minute window per key, in memory.

    Per process: the API runs as a single process, since the model — not the
    API — is the bottleneck. A second process would need this in Redis.
    """

    def __init__(self) -> None:
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    def check(self, key_id: int, limit: int, now: float | None = None) -> int:
        """0 when allowed (and counted), else seconds until the next request may go."""
        now = time.monotonic() if now is None else now
        hits = self._hits[key_id]
        while hits and now - hits[0] >= 60:
            hits.popleft()
        if len(hits) >= limit:
            return int(60 - (now - hits[0])) + 1
        hits.append(now)
        return 0


async def authenticate(request: Request) -> ApiKey:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "Missing API key: send 'Authorization: Bearer <key>'.",
                            headers={"WWW-Authenticate": "Bearer"})
    store = request.app.state.store
    key = await store.find_key(hash_key(token.strip()))
    if key is None or key.revoked:
        raise HTTPException(401, "Invalid or revoked API key.", headers={"WWW-Authenticate": "Bearer"})

    retry_after = request.app.state.rate_limiter.check(key.id, key.rate_limit_per_minute)
    if retry_after:
        raise HTTPException(429, f"Rate limit of {key.rate_limit_per_minute} requests/minute reached.",
                            headers={"Retry-After": str(retry_after)})
    await store.touch_key(key.id)
    return key
