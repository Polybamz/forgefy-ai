"""The admin console: its API, its protection, and the static page."""
from __future__ import annotations

import httpx
import pytest

from app import admin
from app.auth import hash_key
from app.config import Settings
from app.main import create_app
from tests.conftest import bearer, completion, make_key

TOKEN = "admin-" + "x" * 40


@pytest.fixture
async def console(store, llm):
    admin._failures.clear()
    settings = Settings(STORE="memory", DEFAULT_MODEL="qwen3:8b", CHAT_MODELS="qwen3:8b,qwen3:4b,mistral",
                        EMBEDDING_MODELS="bge-m3", MODEL_ALIASES="fast=qwen3:4b,embed=bge-m3",
                        ADMIN_TOKEN=TOKEN)
    app = create_app(settings=settings, store=store, llm=llm)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


ADMIN = bearer(TOKEN)


class TestProtection:
    async def test_needs_the_admin_token(self, console, store):
        assert (await console.get("/admin/api/overview")).status_code == 401
        assert (await console.get("/admin/api/overview", headers=bearer("wrong"))).status_code == 401
        app_key = await make_key(store, app="forgefy")
        # An app's API key is not an admin token.
        assert (await console.get("/admin/api/overview", headers=bearer(app_key))).status_code == 401

    async def test_locks_out_after_repeated_failures(self, console):
        for _ in range(admin._MAX_FAILURES):
            await console.get("/admin/api/overview", headers=bearer("guess"))
        resp = await console.get("/admin/api/overview", headers=ADMIN)  # even the right one, for now
        assert resp.status_code == 429 and resp.headers["retry-after"]

    async def test_switched_off_without_a_token(self, client):
        resp = await client.get("/admin/api/overview", headers=bearer("anything"))
        assert resp.status_code == 503 and "ADMIN_TOKEN" in resp.json()["detail"]

    async def test_page_is_served_with_a_strict_csp(self, console):
        assert (await console.get("/admin")).status_code in (301, 302, 307, 308)
        page = await console.get("/admin/")
        assert page.status_code == 200 and "Model server" in page.text
        csp = page.headers["content-security-policy"]
        assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
        assert "unsafe-inline" not in csp
        assert (await console.get("/admin/admin.js")).status_code == 200
        assert (await console.get("/admin/admin.css")).status_code == 200


class TestPages:
    async def test_overview(self, console, store, llm):
        llm.chat_replies.append(completion("hi"))
        key = await make_key(store, app="vndor")
        await console.post("/v1/chat/completions", headers=bearer(key),
                           json={"model": "fast", "messages": [{"role": "user", "content": "hi"}]})
        d = (await console.get("/admin/api/overview", headers=ADMIN)).json()
        assert d["runtime"] == "ok" and d["keys_active"] == 1
        assert d["last_24h"]["requests"] == 1 and d["last_24h"]["apps"] == ["vndor"]
        assert d["roles"] == {"fast": "qwen3:4b", "embed": "bge-m3"}
        assert d["models_missing"] == ["qwen3:4b"]

    async def test_models(self, console):
        d = (await console.get("/admin/api/models", headers=ADMIN)).json()
        by = {m["name"]: m for m in d["models"]}
        assert by["qwen3:8b"]["default"] and by["qwen3:8b"]["installed"]
        assert by["qwen3:4b"]["roles"] == ["fast"] and not by["qwen3:4b"]["installed"]
        assert by["mistral"]["installed"]  # Ollama calls it mistral:latest
        assert by["bge-m3"]["kind"] == "embed" and by["bge-m3"]["roles"] == ["embed"]
        assert "thinking" in by["qwen3:8b"]["capabilities"]

    async def test_create_list_revoke_keys(self, console, store):
        resp = await console.post("/admin/api/keys", headers=ADMIN,
                                  json={"app": "acme-corp", "environment": "staging", "rate_limit_per_minute": 90})
        assert resp.status_code == 200
        created = resp.json()
        assert created["key"].startswith("aik_") and created["rate_limit_per_minute"] == 90
        assert (await store.find_key(hash_key(created["key"]))).app == "acme-corp"

        listed = (await console.get("/admin/api/keys", headers=ADMIN)).json()["keys"]
        assert [k["prefix"] for k in listed] == [created["prefix"]]
        assert "key" not in listed[0]  # never shown again

        assert (await console.post(f"/admin/api/keys/{created['prefix']}/revoke", headers=ADMIN)).status_code == 200
        assert (await console.post(f"/admin/api/keys/{created['prefix']}/revoke", headers=ADMIN)).status_code == 404
        assert (await console.get("/api/v1/health", headers=bearer(created["key"]))).status_code == 401

    @pytest.mark.parametrize("body", [{"app": "Bad Name"}, {"app": "ok", "environment": "prod"},
                                      {"app": "ok", "rate_limit_per_minute": 0}])
    async def test_key_validation(self, console, body):
        assert (await console.post("/admin/api/keys", headers=ADMIN, json=body)).status_code == 422

    async def test_usage_daily(self, console, store, llm):
        key = await make_key(store, app="forgefy")
        llm.chat_replies += [completion("a"), completion("b")]
        for _ in range(2):
            await console.post("/v1/chat/completions", headers=bearer(key),
                               json={"model": "qwen3:8b", "messages": [{"role": "user", "content": "x"}]})
        d = (await console.get("/admin/api/usage?days=7", headers=ADMIN)).json()
        [day] = d["daily"]
        assert (day["app"], day["requests"], day["errors"], day["completion_tokens"]) == ("forgefy", 2, 0, 10)
        assert d["summary"][0]["requests"] == 2

    async def test_playground_streams_and_is_logged_as_admin(self, console, store, llm):
        llm.stream_lines = [
            b'{"model":"qwen3:4b","message":{"role":"assistant","content":"Bonjour"},"done":false}\n',
            b'{"model":"qwen3:4b","done":true,"prompt_eval_count":9,"eval_count":2,"eval_duration":500000000}\n',
        ]
        resp = await console.post("/admin/api/playground", headers=ADMIN, json={
            "model": "fast", "think": False, "messages": [{"role": "user", "content": "Say hello in French"}]})
        assert resp.status_code == 200 and "Bonjour" in resp.text
        assert llm.chat_calls[0]["body"]["model"] == "qwen3:4b"
        record = store.usage[-1]
        assert (record.app, record.task, record.key_id, record.completion_tokens) == (
            "admin-console", "playground", None, 2)
