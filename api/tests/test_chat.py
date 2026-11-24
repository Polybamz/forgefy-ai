"""The model service: OpenAI- and Anthropic-compatible pass-through."""
from __future__ import annotations

import json

from app.llm import ModelBusy, ModelError
from tests.conftest import bearer, completion, make_key, tool_call

WEATHER_TOOL = {"type": "function", "function": {
    "name": "get_weather", "description": "Weather for a city",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
}}

ANTHROPIC_REPLY = {
    "id": "msg_1", "type": "message", "role": "assistant", "model": "qwen3:8b",
    "content": [{"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": "a.py"}}],
    "stop_reason": "tool_use", "usage": {"input_tokens": 120, "output_tokens": 30},
}


async def _chat(client, key, **body):
    body.setdefault("messages", [{"role": "user", "content": "hi"}])
    return await client.post("/v1/chat/completions", json=body, headers=bearer(key))


async def _messages(client, key, headers=None, **body):
    body.setdefault("messages", [{"role": "user", "content": "hi"}])
    body.setdefault("max_tokens", 100)
    return await client.post("/v1/messages", json=body, headers=headers or {"x-api-key": key})


class TestModels:
    async def test_lists_models_roles_and_capabilities(self, client, store):
        key = await make_key(store, app="acme")
        data = (await client.get("/v1/models", headers=bearer(key))).json()["data"]
        assert [m["id"] for m in data] == ["qwen3:8b", "qwen3:4b", "mistral", "fast", "bge-m3", "embed"]
        by_id = {m["id"]: m for m in data}
        assert by_id["mistral"]["capabilities"] == ["completion", "tools"]
        assert by_id["fast"]["alias_of"] == "qwen3:4b"
        assert by_id["bge-m3"]["kind"] == "embed"

    async def test_role_names(self, client, store, llm):
        key = await make_key(store, app="acme")
        llm.chat_replies.append(completion("ok"))
        await _chat(client, key, model="fast")
        assert llm.chat_calls[0]["body"]["model"] == "qwen3:4b"

    async def test_thinking_setting_only_sent_to_models_that_think(self, client, store, llm):
        key = await make_key(store, app="acme")  # THINKING=off in the fixture
        llm.chat_replies += [completion("a"), completion("b")]
        await _chat(client, key, model="qwen3:8b")
        await _chat(client, key, model="mistral")  # can't think: Ollama would reject the setting
        assert llm.chat_calls[0]["body"]["reasoning_effort"] == "none"
        assert "reasoning_effort" not in llm.chat_calls[1]["body"]

    async def test_needs_the_chat_scope(self, client, store, llm):
        key = await make_key(store, app="vndor", scopes=["agents"])
        resp = await _chat(client, key, model="qwen3:8b")
        assert resp.status_code == 403
        assert resp.json()["error"]["message"].startswith("This key does not have")  # OpenAI error shape
        assert llm.chat_calls == []

    async def test_unlisted_model_is_refused(self, client, store, llm):
        key = await make_key(store, app="acme")
        resp = await _chat(client, key, model="llama3:70b")
        assert resp.status_code == 422
        assert "Available: qwen3:8b, qwen3:4b, mistral, fast" in resp.json()["error"]["message"]
        assert llm.chat_calls == []

    async def test_default_alias_and_aliases(self, client, store, llm):
        key = await make_key(store, app="acme")
        llm.chat_replies += [completion("a"), completion("b"), completion("c")]
        await _chat(client, key, model="default")
        await _chat(client, key)
        await _chat(client, key, model="claude-sonnet-4-5")
        assert [c["body"]["model"] for c in llm.chat_calls] == ["qwen3:8b"] * 3


class TestOpenAIFormat:
    async def test_request_passes_through_unchanged(self, client, store, llm):
        """System instructions, the caller's tools and options reach the model as sent."""
        key = await make_key(store, app="acme")
        llm.chat_replies.append(completion(tool_calls=[tool_call("get_weather", {"city": "Douala"})]))
        messages = [{"role": "system", "content": "You are terse."}, {"role": "user", "content": "Weather?"}]
        resp = await _chat(client, key, model="qwen3:8b", messages=messages, tools=[WEATHER_TOOL],
                           tool_choice="auto", temperature=0.1, response_format={"type": "json_object"})
        assert resp.status_code == 200
        call = llm.chat_calls[0]
        assert call["path"] == "/v1/chat/completions"
        sent = call["body"]
        assert sent["messages"] == messages and sent["tools"] == [WEATHER_TOOL]
        assert sent["temperature"] == 0.1 and sent["response_format"] == {"type": "json_object"}
        # The caller's tool call comes back for the caller to run.
        assert resp.json()["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "get_weather"
        assert store.usage[-1].task == "chat" and store.usage[-1].completion_tokens == 5

    async def test_thinking_off_setting_applies_unless_asked(self, client, store, llm):
        key = await make_key(store, app="acme")  # the fixture sets THINKING=off
        llm.chat_replies += [completion("a"), completion("b")]
        await _chat(client, key, model="qwen3:8b")
        await _chat(client, key, model="qwen3:8b", reasoning_effort="high")
        assert llm.chat_calls[0]["body"]["reasoning_effort"] == "none"
        assert llm.chat_calls[1]["body"]["reasoning_effort"] == "high"

    async def test_thinking_model_setting_leaves_requests_alone(self, client, store, llm, settings):
        settings.THINKING = "model"
        key = await make_key(store, app="acme")
        llm.chat_replies.append(completion("a"))
        await _chat(client, key, model="qwen3:8b")
        assert "reasoning_effort" not in llm.chat_calls[0]["body"]

    async def test_streaming_is_relayed_and_usage_recorded(self, client, store, llm):
        key = await make_key(store, app="acme")
        llm.stream_lines = [
            b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n', b"\n",
            b'data: {"choices":[{"delta":{"content":"lo"}}]}\n', b"\n",
            b'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":2}}\n', b"\n",
            b"data: [DONE]\n", b"\n",
        ]
        resp = await _chat(client, key, model="qwen3:8b", stream=True)
        assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/event-stream")
        assert '"Hel"' in resp.text and resp.text.rstrip().endswith("data: [DONE]")
        assert (store.usage[-1].prompt_tokens, store.usage[-1].completion_tokens) == (3, 2)

    async def test_runtime_rejection_keeps_its_status(self, client, store, llm):
        key = await make_key(store, app="acme")
        llm.chat_replies.append(ModelError("qwen3:8b does not support tools", status=400))
        resp = await _chat(client, key, model="qwen3:8b")
        assert resp.status_code == 400 and "does not support tools" in resp.json()["error"]["message"]

    async def test_busy_is_503_with_retry_after(self, client, store, llm):
        key = await make_key(store, app="acme")
        llm.chat_replies.append(ModelBusy())
        resp = await _chat(client, key, model="qwen3:8b")
        assert resp.status_code == 503 and resp.headers["retry-after"] == "30"


class TestAnthropicFormat:
    async def test_claude_code_style_request(self, client, store, llm):
        """x-api-key auth, a claude-* model name, the client's own tools — passed through."""
        key = await make_key(store, app="acme")
        llm.chat_replies.append(ANTHROPIC_REPLY)
        tools = [{"name": "Read", "description": "Read a file",
                  "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}}}]
        resp = await _messages(client, key, model="claude-sonnet-4-5", system="You are a coding agent.",
                               tools=tools)
        assert resp.status_code == 200, resp.text
        assert resp.json()["content"][0]["type"] == "tool_use"
        call = llm.chat_calls[0]
        assert call["path"] == "/v1/messages"
        assert call["body"]["model"] == "qwen3:8b" and call["body"]["tools"] == tools
        assert call["body"]["system"] == "You are a coding agent."
        assert call["body"]["thinking"] == {"type": "disabled"}
        assert (store.usage[-1].task, store.usage[-1].prompt_tokens, store.usage[-1].completion_tokens) == (
            "messages", 120, 30)

    async def test_bearer_auth_works_too(self, client, store, llm):
        key = await make_key(store, app="acme")
        llm.chat_replies.append(ANTHROPIC_REPLY)
        resp = await _messages(client, key, headers=bearer(key), model="qwen3:8b")
        assert resp.status_code == 200

    async def test_errors_use_the_anthropic_shape(self, client, store):
        resp = await _messages(client, "aik_wrong", model="qwen3:8b")
        assert resp.status_code == 401
        assert resp.json() == {"type": "error", "error": {
            "type": "authentication_error", "message": "Invalid or revoked API key."}}

    async def test_thinking_left_alone_when_requested(self, client, store, llm):
        key = await make_key(store, app="acme")
        llm.chat_replies.append(ANTHROPIC_REPLY)
        await _messages(client, key, model="qwen3:8b", thinking={"type": "enabled", "budget_tokens": 2000})
        assert llm.chat_calls[0]["body"]["thinking"]["type"] == "enabled"

    async def test_streaming_usage_from_start_and_delta_events(self, client, store, llm):
        key = await make_key(store, app="acme")
        llm.stream_lines = [
            b"event: message_start\n",
            b'data: {"type":"message_start","message":{"usage":{"input_tokens":50,"output_tokens":1}}}\n', b"\n",
            b"event: content_block_delta\n",
            b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hi"}}\n', b"\n",
            b"event: message_delta\n",
            b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":7}}\n', b"\n",
            b"event: message_stop\n", b'data: {"type":"message_stop"}\n', b"\n",
        ]
        resp = await _messages(client, key, model="qwen3:8b", stream=True)
        assert "event: message_stop" in resp.text
        assert (store.usage[-1].prompt_tokens, store.usage[-1].completion_tokens) == (50, 7)

    async def test_count_tokens_estimate(self, client, store, llm):
        key = await make_key(store, app="acme")
        resp = await client.post("/v1/messages/count_tokens", headers={"x-api-key": key}, json={
            "model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "x" * 400}]})
        assert resp.status_code == 200 and 100 <= resp.json()["input_tokens"] <= 130
        assert llm.chat_calls == []


class TestOllamaFormat:
    """Ollama's own API — so Forgefy's backend (and any Ollama client) can point here."""

    async def test_streams_ndjson_by_default_with_usage(self, client, store, llm):
        key = await make_key(store, app="forgefy")
        llm.stream_lines = [
            b'{"model":"qwen3:8b","message":{"role":"assistant","content":"Hel"},"done":false}\n',
            b'{"model":"qwen3:8b","message":{"role":"assistant","content":"lo"},"done":false}\n',
            b'{"model":"qwen3:8b","done":true,"prompt_eval_count":12,"eval_count":4}\n',
        ]
        resp = await client.post("/api/chat", headers=bearer(key), json={
            "model": "qwen3:8b", "messages": [{"role": "user", "content": "hi"}],
            "options": {"num_ctx": 8192, "num_predict": 100}})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/x-ndjson")
        assert len(resp.text.splitlines()) == 3
        sent = llm.chat_calls[0]
        assert sent["path"] == "/api/chat" and sent["body"]["options"] == {"num_ctx": 8192, "num_predict": 100}
        assert sent["body"]["think"] is False  # THINKING=off in the test settings
        assert (store.usage[-1].task, store.usage[-1].prompt_tokens, store.usage[-1].completion_tokens) == (
            "ollama-chat", 12, 4)

    async def test_non_streaming(self, client, store, llm):
        key = await make_key(store, app="forgefy")
        llm.chat_replies.append({"model": "qwen3:8b", "message": {"role": "assistant", "content": "ok"},
                                 "done": True, "prompt_eval_count": 5, "eval_count": 1})
        resp = await client.post("/api/chat", headers=bearer(key), json={
            "model": "qwen3:8b", "stream": False, "format": "json",
            "messages": [{"role": "user", "content": "hi"}]})
        assert resp.json()["message"]["content"] == "ok"
        assert llm.chat_calls[0]["body"]["format"] == "json"

    async def test_tags_and_ollama_error_shape(self, client, store):
        key = await make_key(store, app="forgefy")
        tags = (await client.get("/api/tags", headers=bearer(key))).json()
        assert [m["name"] for m in tags["models"]] == ["qwen3:8b", "qwen3:4b", "mistral", "fast", "bge-m3", "embed"]
        resp = await client.post("/api/chat", headers=bearer("aik_wrong"), json={"model": "qwen3:8b"})
        assert resp.status_code == 401 and resp.json() == {"error": "Invalid or revoked API key."}


class TestEmbeddings:
    async def test_openai_format(self, client, store, llm):
        key = await make_key(store, app="acme")
        llm.chat_replies.append({"object": "list", "data": [{"embedding": [0.1, 0.2], "index": 0}],
                                 "usage": {"prompt_tokens": 7, "total_tokens": 7}})
        resp = await client.post("/v1/embeddings", headers=bearer(key),
                                 json={"model": "embed", "input": ["raffia tote", "savon karité"]})
        assert resp.status_code == 200 and resp.json()["data"][0]["embedding"] == [0.1, 0.2]
        call = llm.chat_calls[0]
        assert call["path"] == "/v1/embeddings" and call["body"]["model"] == "bge-m3"
        assert (store.usage[-1].task, store.usage[-1].prompt_tokens) == ("embeddings", 7)

    async def test_ollama_format_default_model(self, client, store, llm):
        key = await make_key(store, app="forgefy")
        llm.chat_replies.append({"embeddings": [[0.3]], "prompt_eval_count": 3})
        resp = await client.post("/api/embed", headers=bearer(key), json={"input": "hello"})
        assert resp.status_code == 200
        assert llm.chat_calls[0]["body"]["model"] == "bge-m3"

    async def test_chat_and_embedding_models_are_kept_apart(self, client, store, llm):
        key = await make_key(store, app="acme")
        resp = await client.post("/v1/embeddings", headers=bearer(key), json={"model": "qwen3:8b", "input": "x"})
        assert resp.status_code == 422 and "Available: bge-m3, embed" in resp.json()["error"]["message"]
        resp = await _chat(client, key, model="bge-m3")
        assert resp.status_code == 422
        assert llm.chat_calls == []

    async def test_input_required(self, client, store):
        key = await make_key(store, app="acme")
        resp = await client.post("/v1/embeddings", headers=bearer(key), json={"model": "embed"})
        assert resp.status_code == 422


class TestKeys:
    def test_cli_customer_key_gets_chat_only(self, capsys):
        import asyncio

        from app import cli
        from app.store import MemoryStore

        store = MemoryStore()
        asyncio.run(cli.main(["keys", "create", "--app", "acme-corp", "--env", "production", "--rate", "60"], store))
        [key] = asyncio.run(store.list_keys())
        assert key.scopes == ["chat"] and key.rate_limit_per_minute == 60


def test_model_resolution():
    from app.config import Settings

    s = Settings(DEFAULT_MODEL="qwen3:8b", CHAT_MODELS="qwen3:8b,qwen3:4b",
                 MODEL_ALIASES="claude-*-haiku-*=qwen3:4b,claude-*=qwen3:8b,gpt-*=llama3:70b")
    assert s.resolve_model("claude-3-5-haiku-20241022") == "qwen3:4b"
    assert s.resolve_model("claude-opus-4") == "qwen3:8b"
    assert s.resolve_model("gpt-4o") is None  # alias target not offered
    assert s.resolve_model("qwen3:4b") == "qwen3:4b"
    assert s.resolve_model("mistral") is None
    assert json.dumps(s.chat_models()) == '["qwen3:8b", "qwen3:4b"]'

    e = Settings(EMBEDDING_MODELS="bge-m3,nomic-embed-text", MODEL_ALIASES="embed=bge-m3,fast=qwen3:4b")
    assert e.resolve_model("default", "embed") == "bge-m3"
    assert e.resolve_model("embed", "embed") == "bge-m3"
    assert e.resolve_model("embed") is None  # not a chat model
    assert e.role_names("embed") == {"embed": "bge-m3"}
    assert Settings().resolve_model("default", "embed") is None  # no embedding models offered
