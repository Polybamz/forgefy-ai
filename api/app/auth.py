"""API keys and per-key rate limits.

A key belongs to one application (an app of yours, a customer, a tool) and one
environment, so usage can be reported per application. Only a SHA-256 hash of
each key is stored; the key itself is shown once, when it is created.
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


def _presented_key(request: Request) -> str:
    """The key from 'Authorization: Bearer …' (OpenAI style) or 'x-api-key' (Anthropic style)."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    return request.headers.get("x-api-key", "").strip()


async def authenticate(request: Request) -> ApiKey:
    token = _presented_key(request)
    if not token:
        raise HTTPException(401, "Missing API key: send 'Authorization: Bearer <key>' or 'x-api-key: <key>'.",
                            headers={"WWW-Authenticate": "Bearer"})
    store = request.app.state.store
    key = await store.find_key(hash_key(token))
    if key is None or key.revoked:
        raise HTTPException(401, "Invalid or revoked API key.", headers={"WWW-Authenticate": "Bearer"})

    retry_after = request.app.state.rate_limiter.check(key.id, key.rate_limit_per_minute)
    if retry_after:
        raise HTTPException(429, f"Rate limit of {key.rate_limit_per_minute} requests/minute reached.",
                            headers={"Retry-After": str(retry_after)})
    await store.touch_key(key.id)
    return key
