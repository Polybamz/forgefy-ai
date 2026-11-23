from __future__ import annotations

from app.llm import ModelBusy, ModelError, inline_schema
from app.agents.forgefy_requirements import RequirementsOutput
from tests.conftest import bearer, make_key

TRANSCRIPT = (
    "Amina: Patients must be able to book an appointment online.\n"
    "Paul: And they need an SMS reminder the day before.\n"
    "Amina: Payments can come later, that's nice to have."
)

GOOD_REQUIREMENTS = {
    "summary": "An online booking system for a clinic.",
    "user_roles": ["patient", "receptionist"],
    "problems": ["Appointments get lost"],
    "goals": ["Patients book online"],
    "functional_requirements": [
        {"id": "FR-1", "description": "Online booking", "priority": "must",
         "source_quote": "Patients must be able to book an appointment online"},
        {"id": "FR-2", "description": "SMS reminder", "priority": "should",
         "source_quote": "they need an SMS reminder the day before"},
        {"id": "FR-3", "description": "Card payments", "priority": "could",
         "source_quote": "we will accept Visa cards"},  # not in the transcript
    ],
    "constraints": [],
    "acceptance_criteria": [{"requirement_id": "FR-1", "criterion": "A patient can book a slot"},
                            {"requirement_id": "FR-9", "criterion": "orphan"}],
    "open_questions": ["Which payment methods?"],
    "language": "en",
}

PRODUCT = {"name": "Raffia tote", "facts": {"material": "raffia", "size": "40 x 30 cm"}, "language": "en"}


class TestAuth:
    async def test_healthz_needs_no_key(self, client):
        assert (await client.get("/healthz")).status_code == 200

    async def test_missing_key(self, client):
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 401
        assert resp.headers["www-authenticate"] == "Bearer"

    async def test_wrong_key(self, client):
        assert (await client.get("/api/v1/health", headers=bearer("aik_nope"))).status_code == 401

    async def test_revoked_key(self, client, store):
        key = await make_key(store)
        await store.revoke_key(key[:12])
        assert (await client.get("/api/v1/health", headers=bearer(key))).status_code == 401

    async def test_key_is_limited_to_its_app(self, client, store, llm):
        key = await make_key(store, app="forgefy")
        resp = await client.post("/api/v1/agents/vndor/product-description", json=PRODUCT, headers=bearer(key))
        assert resp.status_code == 403
        assert llm.calls == []

    async def test_rate_limit(self, client, store):
        key = await make_key(store, rate=2)
        codes = [(await client.get("/api/v1/health", headers=bearer(key))).status_code for _ in range(3)]
        assert codes == [200, 200, 429]

    async def test_health_reports_this_apps_agents(self, client, store):
        key = await make_key(store, app="vndor")
        body = (await client.get("/api/v1/health", headers=bearer(key))).json()
        assert body["app"] == "vndor"
        assert list(body["agents"]) == ["vndor.product-description"]
        assert body["agents"]["vndor.product-description"]["model_downloaded"] is True


class TestRequirementsAgent:
    async def test_result_envelope_and_quote_checks(self, client, store, llm):
        key = await make_key(store)
        llm.reply(GOOD_REQUIREMENTS)
        resp = await client.post("/api/v1/agents/forgefy/requirements",
                                 json={"transcript": TRANSCRIPT}, headers=bearer(key))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["agent"] == "forgefy.requirements" and body["model"] == "qwen3:8b"
        reqs = {r["id"]: r["quote_verified"] for r in body["result"]["functional_requirements"]}
        assert reqs == {"FR-1": True, "FR-2": True, "FR-3": False}
        assert any("FR-3" in w for w in body["warnings"])
        assert any("FR-9" in w for w in body["warnings"])
        assert body["usage"]["tokens_per_second"] == 25.0

    async def test_model_gets_a_schema_without_refs(self, client, store, llm):
        key = await make_key(store)
        llm.reply(GOOD_REQUIREMENTS)
        await client.post("/api/v1/agents/forgefy/requirements", json={"transcript": TRANSCRIPT}, headers=bearer(key))
        assert "$ref" not in str(llm.calls[0]["schema"])

    async def test_invalid_json_gets_one_retry(self, client, store, llm):
        key = await make_key(store)
        llm.reply("{not json", GOOD_REQUIREMENTS)
        resp = await client.post("/api/v1/agents/forgefy/requirements",
                                 json={"transcript": TRANSCRIPT}, headers=bearer(key))
        assert resp.status_code == 200
        assert resp.json()["usage"]["attempts"] == 2
        assert "does not match the required schema" in llm.calls[1]["messages"][-1]["content"]

    async def test_invalid_twice_is_502_and_logged(self, client, store, llm):
        key = await make_key(store)
        llm.reply({"summary": "x"}, {"summary": "x"})
        resp = await client.post("/api/v1/agents/forgefy/requirements",
                                 json={"transcript": TRANSCRIPT}, headers=bearer(key))
        assert resp.status_code == 502
        assert store.usage[-1].status == "invalid_output"

    async def test_too_long_transcript_is_rejected_before_the_model(self, client, store, llm):
        key = await make_key(store)
        resp = await client.post("/api/v1/agents/forgefy/requirements",
                                 json={"transcript": "word " * 1000}, headers=bearer(key))
        assert resp.status_code == 422
        assert "segments" in resp.json()["detail"]
        assert llm.calls == []

    async def test_french_output_is_requested(self, client, store, llm):
        key = await make_key(store)
        llm.reply({**GOOD_REQUIREMENTS, "language": "fr"})
        await client.post("/api/v1/agents/forgefy/requirements",
                          json={"transcript": TRANSCRIPT, "language": "fr"}, headers=bearer(key))
        assert "in French" in llm.calls[0]["messages"][0]["content"]


class TestProductAgent:
    async def test_invented_numbers_are_flagged(self, client, store, llm):
        key = await make_key(store, app="vndor")
        llm.reply({
            "short_description": "A raffia tote, now only 5000 FCFA.",
            "long_description": "Woven raffia, 40 x 30 cm, perfect for the market.",
            "highlights": ["Raffia", "40 x 30 cm", "Ships in 2 days"],
        })
        resp = await client.post("/api/v1/agents/vndor/product-description", json=PRODUCT, headers=bearer(key))
        assert resp.status_code == 200
        [warning] = resp.json()["warnings"]
        assert "2" in warning and "5000" in warning and "40" not in warning

    async def test_clean_description_has_no_warnings(self, client, store, llm):
        key = await make_key(store, app="vndor")
        llm.reply({
            "short_description": "A woven raffia tote.",
            "long_description": "Natural raffia, 40 x 30 cm.",
            "highlights": ["Raffia", "40 x 30 cm", "Handmade look"],
        })
        resp = await client.post("/api/v1/agents/vndor/product-description", json=PRODUCT, headers=bearer(key))
        assert resp.json()["warnings"] == []

    async def test_bad_input_is_422(self, client, store):
        key = await make_key(store, app="vndor")
        resp = await client.post("/api/v1/agents/vndor/product-description",
                                 json={"name": "", "language": "de"}, headers=bearer(key))
        assert resp.status_code == 422


class TestRuntimeFailures:
    async def test_busy_model_is_503_with_retry_after(self, client, store, llm):
        key = await make_key(store, app="vndor")
        llm.reply(ModelBusy())
        resp = await client.post("/api/v1/agents/vndor/product-description", json=PRODUCT, headers=bearer(key))
        assert resp.status_code == 503
        assert resp.headers["retry-after"] == "30"
        assert store.usage[-1].status == "busy"

    async def test_missing_model_is_503_with_reason(self, client, store, llm):
        key = await make_key(store, app="vndor")
        llm.reply(ModelError("model 'qwen3:8b' is not downloaded on this server"))
        resp = await client.post("/api/v1/agents/vndor/product-description", json=PRODUCT, headers=bearer(key))
        assert resp.status_code == 503
        assert "not downloaded" in resp.json()["detail"]


class TestUsage:
    async def test_usage_is_per_app_and_content_free(self, client, store, llm):
        forgefy, vndor = await make_key(store, app="forgefy"), await make_key(store, app="vndor")
        llm.reply(GOOD_REQUIREMENTS)
        await client.post("/api/v1/agents/forgefy/requirements", json={"transcript": TRANSCRIPT},
                          headers=bearer(forgefy))
        llm.reply(ModelBusy())
        await client.post("/api/v1/agents/vndor/product-description", json=PRODUCT, headers=bearer(vndor))

        rows = (await client.get("/api/v1/usage", headers=bearer(forgefy))).json()["rows"]
        assert [(r["app"], r["task"], r["status"], r["requests"]) for r in rows] == [
            ("forgefy", "requirements", "ok", 1),
        ]
        # The log holds metadata only — never the transcript.
        assert all("Patients" not in str(vars(r)) for r in store.usage)


def test_model_overrides():
    from app.config import Settings

    s = Settings(DEFAULT_MODEL="qwen3:8b", MODEL_OVERRIDES="vndor.product-description=qwen3:4b, bad, x=")
    assert s.model_for("vndor.product-description") == "qwen3:4b"
    assert s.model_for("forgefy.requirements") == "qwen3:8b"


def test_inline_schema_resolves_nested_models():
    schema = inline_schema(RequirementsOutput.model_json_schema())
    assert "$defs" not in schema and "$ref" not in str(schema)
    item = schema["properties"]["functional_requirements"]["items"]
    assert set(item["properties"]) == {"id", "description", "priority", "source_quote"}
