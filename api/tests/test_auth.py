"""Keys, rate limits, health and usage endpoints."""
from __future__ import annotations

from tests.conftest import bearer, completion, make_key


async def test_healthz_needs_no_key(client):
    assert (await client.get("/healthz")).status_code == 200


async def test_missing_key(client):
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


async def test_wrong_key(client):
    assert (await client.get("/api/v1/health", headers=bearer("aik_nope"))).status_code == 401


async def test_revoked_key(client, store):
    key = await make_key(store)
    await store.revoke_key(key[:12])
    assert (await client.get("/api/v1/health", headers=bearer(key))).status_code == 401


async def test_rate_limit(client, store):
    key = await make_key(store, rate=2)
    codes = [(await client.get("/api/v1/health", headers=bearer(key))).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


async def test_health_reports_roles_and_missing_models(client, store, llm):
    key = await make_key(store, app="acme")
    body = (await client.get("/api/v1/health", headers=bearer(key))).json()
    assert body["app"] == "acme" and body["runtime"] == "ok"
    assert body["models_offered"] == ["qwen3:8b", "qwen3:4b", "mistral", "bge-m3"]
    assert body["roles"] == {"fast": "qwen3:4b", "embed": "bge-m3"}
    # The fake runtime has qwen3:8b, mistral:latest and bge-m3:latest.
    assert body["models_missing"] == ["qwen3:4b"]


async def test_usage_is_per_app_and_content_free(client, store, llm):
    mine, theirs = await make_key(store, app="forgefy"), await make_key(store, app="acme")
    llm.chat_replies += [completion("a"), completion("b")]
    secret = "the launch date is 3 March"
    for key in (mine, theirs):
        await client.post("/v1/chat/completions", headers=bearer(key),
                          json={"model": "qwen3:8b", "messages": [{"role": "user", "content": secret}]})
    rows = (await client.get("/api/v1/usage", headers=bearer(mine))).json()["rows"]
    assert [(r["app"], r["task"], r["requests"]) for r in rows] == [("forgefy", "chat", 1)]
    assert all(secret not in str(vars(r)) for r in store.usage)  # metadata only, never content
