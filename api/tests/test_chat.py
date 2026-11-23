"""The general /v1 API: passthrough chat, client tools, skills, plugins, scopes."""
from __future__ import annotations

import json

from app.llm import ModelError
from tests.conftest import bearer, completion, make_key, tool_call

WEATHER_TOOL = {"type": "function", "function": {
    "name": "get_weather", "description": "Weather for a city",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
}}


async def _chat(client, key, **body):
    body.setdefault("messages", [{"role": "user", "content": "hi"}])
    return await client.post("/v1/chat/completions", json=body, headers=bearer(key))


class TestModels:
    async def test_lists_the_allowlist(self, client, store):
        key = await make_key(store, app="n8n", scopes=["chat"])
        data = (await client.get("/v1/models", headers=bearer(key))).json()["data"]
        assert [m["id"] for m in data] == ["qwen3:8b", "qwen3:4b"]

    async def test_chat_needs_the_chat_scope(self, client, store, llm):
        key = await make_key(store, app="n8n", scopes=["skills"])
        assert (await _chat(client, key, model="qwen3:8b")).status_code == 403
        assert llm.chat_calls == []

    async def test_unlisted_model_is_refused(self, client, store, llm):
        key = await make_key(store, app="n8n")
        resp = await _chat(client, key, model="llama3:70b")
        assert resp.status_code == 422 and "Available: qwen3:8b, qwen3:4b" in resp.json()["detail"]
        assert llm.chat_calls == []

    async def test_default_alias_and_missing_model(self, client, store, llm):
        key = await make_key(store, app="n8n")
        llm.chat_replies += [completion("a"), completion("b")]
        await _chat(client, key, model="default")
        await _chat(client, key)
        assert [c["model"] for c in llm.chat_calls] == ["qwen3:8b", "qwen3:8b"]


class TestPassthrough:
    async def test_system_instructions_and_client_tools_go_to_the_model_unchanged(self, client, store, llm):
        key = await make_key(store, app="n8n")
        llm.chat_replies.append(completion(tool_calls=[tool_call("get_weather", {"city": "Douala"})]))
        messages = [{"role": "system", "content": "You are terse."}, {"role": "user", "content": "Weather?"}]
        resp = await _chat(client, key, model="qwen3:8b", messages=messages, tools=[WEATHER_TOOL],
                           tool_choice="auto", temperature=0.1)
        assert resp.status_code == 200
        sent = llm.chat_calls[0]
        assert sent["messages"] == messages and sent["tools"] == [WEATHER_TOOL] and sent["temperature"] == 0.1
        # The caller's tool call comes back for the caller to run.
        call = resp.json()["choices"][0]["message"]["tool_calls"][0]
        assert call["function"]["name"] == "get_weather"
        assert store.usage[-1].task == "chat" and store.usage[-1].completion_tokens == 5

    async def test_streaming_is_relayed(self, client, store, llm):
        key = await make_key(store, app="n8n")
        llm.stream_lines = [
            b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n', b"\n",
            b'data: {"choices":[{"delta":{"content":"lo"}}]}\n', b"\n",
            b'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":2}}\n', b"\n",
            b"data: [DONE]\n", b"\n",
        ]
        resp = await _chat(client, key, model="qwen3:8b", stream=True)
        assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/event-stream")
        assert '"Hel"' in resp.text and resp.text.rstrip().endswith("data: [DONE]")
        assert store.usage[-1].completion_tokens == 2

    async def test_runtime_rejection_keeps_its_status(self, client, store, llm):
        key = await make_key(store, app="n8n")
        llm.chat_replies.append(ModelError("qwen3:8b does not support tools", status=400))
        resp = await _chat(client, key, model="qwen3:8b")
        assert resp.status_code == 400 and "does not support tools" in resp.json()["detail"]


class TestSkills:
    async def test_listed(self, client, store):
        key = await make_key(store, app="n8n")
        skills = (await client.get("/v1/skills", headers=bearer(key))).json()["skills"]
        [skill] = skills
        assert skill["name"] == "cameroon-business-writing" and skill["files"] == ["examples.md"]

    async def test_model_loads_a_skill_then_answers(self, client, store, llm):
        key = await make_key(store, app="vndor")
        llm.chat_replies += [
            completion(tool_calls=[tool_call("load_skill", {"name": "cameroon-business-writing"})]),
            completion("Bonjour 👋 …"),
        ]
        resp = await _chat(client, key, model="qwen3:8b", skills=["cameroon-business-writing"],
                           messages=[{"role": "system", "content": "Be kind."},
                                     {"role": "user", "content": "Write a WhatsApp promo"}])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["choices"][0]["message"]["content"] == "Bonjour 👋 …"
        assert [s["tool"] for s in body["x_tool_steps"]] == ["load_skill"]
        assert body["usage"]["completion_tokens"] == 10  # both rounds counted

        first, second = llm.chat_calls
        system = first["messages"][0]["content"]
        assert system.startswith("Be kind.") and "cameroon-business-writing:" in system
        assert "skills" not in first  # extension fields never reach the model
        tool_result = second["messages"][-1]
        assert tool_result["role"] == "tool" and "FCFA" in tool_result["content"]
        assert "examples.md" in tool_result["content"]

    async def test_unknown_skill(self, client, store):
        key = await make_key(store, app="n8n")
        resp = await _chat(client, key, model="qwen3:8b", skills=["nope"])
        assert resp.status_code == 422

    async def test_skills_need_the_skills_scope(self, client, store):
        key = await make_key(store, app="n8n", scopes=["chat"])
        assert (await _chat(client, key, model="qwen3:8b", skills="all")).status_code == 403

    def test_only_listed_files_are_readable(self, project_skills):
        from app.skills import read_skill

        assert "FCFA" in read_skill(project_skills, "cameroon-business-writing", "examples.md")
        for sneaky in ("../../api/app/config.py", "/etc/passwd", "SKILL.md/../../x"):
            assert read_skill(project_skills, "cameroon-business-writing", sneaky).startswith("error:")


class TestPlugins:
    async def test_plugin_needs_to_be_granted(self, client, store, llm):
        key = await make_key(store, app="n8n")  # default scopes: no plugins
        assert (await _chat(client, key, model="qwen3:8b", plugins=["calculator"])).status_code == 403
        listed = (await client.get("/v1/plugins", headers=bearer(key))).json()["plugins"]
        assert listed == []
        assert llm.chat_calls == []

    async def test_listing_shows_granted_plugins_with_schemas(self, client, store):
        key = await make_key(store, app="n8n", scopes=["chat", "plugin:calculator"])
        [plugin] = (await client.get("/v1/plugins", headers=bearer(key))).json()["plugins"]
        assert plugin["name"] == "calculator"
        [fn] = plugin["tools"]
        assert fn["name"] == "calculator__calculate"
        assert fn["parameters"]["required"] == ["expression"]

    async def test_server_runs_the_tool_and_returns_the_answer(self, client, store, llm):
        key = await make_key(store, app="n8n", scopes=["chat", "plugin:*"])
        llm.chat_replies += [
            completion(tool_calls=[tool_call("calculator__calculate", {"expression": "2500 * 3"}),
                                   tool_call("clock__now", {"timezone": "Africa/Douala"}, "call_2")]),
            completion("Three soaps cost 7 500 FCFA."),
        ]
        resp = await _chat(client, key, model="qwen3:8b", plugins=["calculator", "clock"])
        assert resp.status_code == 200, resp.text
        steps = resp.json()["x_tool_steps"]
        assert [(s["tool"], s["ok"]) for s in steps] == [("calculator__calculate", True), ("clock__now", True)]
        results = [m for m in llm.chat_calls[1]["messages"] if m["role"] == "tool"]
        assert results[0]["content"] == "2500 * 3 = 7500" and results[0]["tool_call_id"] == "call_1"
        assert json.loads(results[1]["content"])["timezone"] == "Africa/Douala"
        sent_tools = {t["function"]["name"] for t in llm.chat_calls[0]["tools"]}
        assert sent_tools == {"calculator__calculate", "clock__now"}

    async def test_tool_errors_go_back_to_the_model(self, client, store, llm):
        key = await make_key(store, app="n8n", scopes=["chat", "plugin:calculator"])
        llm.chat_replies += [
            completion(tool_calls=[tool_call("calculator__calculate", {"expression": "__import__('os')"})]),
            completion("Sorry."),
        ]
        resp = await _chat(client, key, model="qwen3:8b", plugins=["calculator"])
        assert resp.json()["x_tool_steps"][0]["ok"] is False
        assert llm.chat_calls[1]["messages"][-1]["content"].startswith("error:")

    async def test_client_tools_still_come_back_to_the_client(self, client, store, llm):
        key = await make_key(store, app="n8n", scopes=["chat", "plugin:calculator"])
        llm.chat_replies.append(completion(tool_calls=[
            tool_call("calculator__calculate", {"expression": "1+1"}),
            tool_call("get_weather", {"city": "Yaoundé"}, "call_2"),
        ]))
        resp = await _chat(client, key, model="qwen3:8b", plugins=["calculator"], tools=[WEATHER_TOOL])
        message = resp.json()["choices"][0]["message"]
        assert [c["function"]["name"] for c in message["tool_calls"]] == ["get_weather"]
        assert resp.json()["choices"][0]["finish_reason"] == "tool_calls"

    async def test_tool_loop_is_bounded(self, client, store, llm, settings):
        key = await make_key(store, app="n8n", scopes=["chat", "plugin:calculator"])
        loop = completion(tool_calls=[tool_call("calculator__calculate", {"expression": "1+1"})])
        llm.chat_replies += [loop] * settings.MAX_TOOL_STEPS
        resp = await _chat(client, key, model="qwen3:8b", plugins=["calculator"])
        assert resp.status_code == 200
        assert len(llm.chat_calls) == settings.MAX_TOOL_STEPS
        assert llm.chat_calls[-1]["tool_choice"] == "none"
        assert resp.json()["choices"][0]["finish_reason"] == "length"

    async def test_name_clash_with_server_tools(self, client, store):
        key = await make_key(store, app="n8n", scopes=["chat", "plugin:calculator"])
        clash = {**WEATHER_TOOL, "function": {**WEATHER_TOOL["function"], "name": "calculator__calculate"}}
        resp = await _chat(client, key, model="qwen3:8b", plugins=["calculator"], tools=[clash])
        assert resp.status_code == 422

    async def test_stream_with_server_tools(self, client, store, llm):
        key = await make_key(store, app="n8n", scopes=["chat", "plugin:calculator"])
        llm.chat_replies += [
            completion(tool_calls=[tool_call("calculator__calculate", {"expression": "6*7"})]),
            completion("42"),
        ]
        resp = await _chat(client, key, model="qwen3:8b", plugins=["calculator"], stream=True,
                           stream_options={"include_usage": True})
        events = [json.loads(line[6:]) for line in resp.text.splitlines()
                  if line.startswith("data: {")]
        assert events[0]["choices"][0]["delta"]["content"] == "42"
        assert events[1]["choices"][0]["finish_reason"] == "stop"
        assert events[2]["usage"]["completion_tokens"] == 10
        assert resp.text.rstrip().endswith("data: [DONE]")


class TestScopes:
    async def test_agents_need_the_agents_scope(self, client, store, llm):
        key = await make_key(store, app="vndor", scopes=["chat"])
        resp = await client.post("/api/v1/agents/vndor/product-description",
                                 json={"name": "Soap"}, headers=bearer(key))
        assert resp.status_code == 403
