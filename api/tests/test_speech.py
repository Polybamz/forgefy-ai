"""Speech-to-text through the gateway: /v1/audio/transcriptions and /translations."""
from __future__ import annotations

import httpx
import pytest

from app.llm import ModelBusy
from app.speech import SpeechClient, SpeechError, _timestamp
from tests.conftest import bearer, make_key

AUDIO = b"RIFF" + b"\0" * 2000


async def _post(client, key, path="/v1/audio/transcriptions", data=None, audio=AUDIO):
    return await client.post(path, headers=bearer(key), files={"file": ("meeting.mp3", audio, "audio/mpeg")},
                             data=data or {})


class TestTranscriptions:
    async def test_default_json(self, client, store, speech):
        key = await make_key(store, app="forgefy")
        resp = await _post(client, key, data={"model": "whisper-1", "language": "fr", "prompt": "Forgefy"})
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"text": "Bonjour à tous. On commence."}
        call = speech.calls[0]
        assert (call["task"], call["language"], call["prompt"]) == ("transcribe", "fr", "Forgefy")
        assert call["filename"] == "meeting.mp3" and call["size"] == len(AUDIO)
        record = store.usage[-1]
        assert (record.app, record.task, record.model, record.audio_seconds) == (
            "forgefy", "transcription", "large-v3-turbo", 3.25)

    async def test_verbose_json(self, client, store):
        key = await make_key(store)
        body = (await _post(client, key, data={"response_format": "verbose_json"})).json()
        assert body["language"] == "fr" and body["duration"] == 3.25 and len(body["segments"]) == 2

    async def test_text(self, client, store):
        key = await make_key(store)
        resp = await _post(client, key, data={"response_format": "text"})
        assert resp.text == "Bonjour à tous. On commence." and resp.headers["content-type"].startswith("text/plain")

    async def test_srt_and_vtt(self, client, store):
        key = await make_key(store)
        srt = (await _post(client, key, data={"response_format": "srt"})).text
        assert srt.startswith("1\n00:00:00,000 --> 00:00:01,500\nBonjour à tous.\n\n2\n00:00:01,500 --> 00:00:03,250")
        vtt = (await _post(client, key, data={"response_format": "vtt"})).text
        assert vtt.startswith("WEBVTT\n\n00:00:00.000 --> 00:00:01.500\nBonjour à tous.")

    async def test_translation(self, client, store, speech):
        key = await make_key(store)
        resp = await _post(client, key, path="/v1/audio/translations")
        assert resp.status_code == 200
        assert speech.calls[0]["task"] == "translate" and speech.calls[0]["language"] is None
        assert store.usage[-1].task == "translation"


class TestRejections:
    async def test_needs_a_key(self, client):
        resp = await client.post("/v1/audio/transcriptions", files={"file": ("a.mp3", AUDIO)})
        assert resp.status_code == 401
        assert resp.json()["error"]["type"] == "invalid_api_key"  # OpenAI error shape

    async def test_unknown_format(self, client, store, speech):
        key = await make_key(store)
        assert (await _post(client, key, data={"response_format": "docx"})).status_code == 422
        assert speech.calls == []

    async def test_too_big(self, client, store, speech, settings):
        settings.MAX_AUDIO_MB = 0
        key = await make_key(store)
        resp = await _post(client, key)
        assert resp.status_code == 413 and "compress" in resp.json()["error"]["message"]
        assert speech.calls == []

    async def test_empty_file(self, client, store):
        key = await make_key(store)
        assert (await _post(client, key, audio=b"")).status_code == 422

    async def test_bad_audio_is_400_and_logged(self, client, store, speech):
        key = await make_key(store)
        speech.result = SpeechError("Could not process the audio: Invalid data", 400)
        resp = await _post(client, key)
        assert resp.status_code == 400 and "Could not process" in resp.json()["error"]["message"]
        assert store.usage[-1].status == "bad_request"

    async def test_busy(self, client, store, speech):
        key = await make_key(store)
        speech.result = ModelBusy()
        resp = await _post(client, key)
        assert resp.status_code == 503 and resp.headers["retry-after"] == "60"

    async def test_service_down(self, client, store, speech):
        key = await make_key(store)
        speech.result = SpeechError("speech service unreachable: connection refused")
        resp = await _post(client, key)
        assert resp.status_code == 503 and store.usage[-1].status == "model_error"


class TestSpeechClient:
    async def test_forwards_multipart_and_maps_errors(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["body"] = request.content
            if b"broken" in request.content:
                return httpx.Response(400, json={"detail": "Could not process the audio"})
            return httpx.Response(200, json={"text": "ok", "duration": 1.0, "model": "tiny"})

        client = SpeechClient("http://speech", timeout=5, queue_timeout=5, transport=httpx.MockTransport(handler))
        result = await client.transcribe("a.wav", b"audio-bytes", "audio/wav", task="transcribe",
                                         language="en", prompt=None, temperature=0.0)
        assert result["text"] == "ok" and seen["path"] == "/transcribe"
        assert b'name="language"' in seen["body"] and b"audio-bytes" in seen["body"]
        with pytest.raises(SpeechError) as err:
            await client.transcribe("a.wav", b"broken", "audio/wav", task="transcribe",
                                    language=None, prompt=None, temperature=0.0)
        assert err.value.status == 400
        await client.close()


def test_timestamps():
    assert _timestamp(0, ",") == "00:00:00,000"
    assert _timestamp(3725.4567, ".") == "01:02:05.457"
    assert _timestamp(59.9996, ",") == "00:01:00,000"


async def test_health_includes_speech(client, store):
    key = await make_key(store)
    body = (await client.get("/api/v1/health", headers=bearer(key))).json()
    assert body["speech"]["model"] == "large-v3-turbo"
