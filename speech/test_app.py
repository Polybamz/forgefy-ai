"""The speech service, with a stand-in for the Whisper model."""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

import app as speech


class FakeWhisper:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def transcribe(self, path, **kwargs):
        self.calls.append({"path": path, **kwargs})
        with open(path, "rb") as f:
            assert f.read() == b"RIFF-fake-audio"
        segments = iter([SimpleNamespace(start=0.0, end=1.5, text=" Bonjour à tous."),
                         SimpleNamespace(start=1.5, end=3.25, text=" On commence.")])
        return segments, SimpleNamespace(language="fr", language_probability=0.987, duration=3.25)


@pytest.fixture
def fake(monkeypatch):
    model = FakeWhisper()
    monkeypatch.setattr(speech, "_model", None)
    monkeypatch.setattr(speech, "_load_model", lambda: model)
    return model


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=speech.app), base_url="http://s") as c:
        yield c


async def test_transcribes_and_reports_segments(client, fake):
    resp = await client.post("/transcribe", files={"file": ("meeting.wav", b"RIFF-fake-audio", "audio/wav")},
                             data={"language": "fr", "prompt": "Forgefy, Vndor"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["text"] == "Bonjour à tous. On commence."
    assert (body["language"], body["duration"]) == ("fr", 3.25)
    assert body["segments"][1] == {"id": 1, "start": 1.5, "end": 3.25, "text": "On commence."}
    call = fake.calls[0]
    assert call["task"] == "transcribe" and call["language"] == "fr" and call["initial_prompt"] == "Forgefy, Vndor"
    assert call["vad_filter"] is True and call["path"].endswith(".wav")


async def test_translate_task_and_auto_language(client, fake):
    await client.post("/transcribe", files={"file": ("a.mp3", b"RIFF-fake-audio")}, data={"task": "translate"})
    assert fake.calls[0]["task"] == "translate" and fake.calls[0]["language"] is None


async def test_bad_task(client, fake):
    resp = await client.post("/transcribe", files={"file": ("a.wav", b"x")}, data={"task": "summarise"})
    assert resp.status_code == 422


async def test_size_limit(client, fake, monkeypatch):
    monkeypatch.setattr(speech, "MAX_UPLOAD_BYTES", 4)
    resp = await client.post("/transcribe", files={"file": ("a.wav", b"RIFF-fake-audio")})
    assert resp.status_code == 413 and fake.calls == []


async def test_undecodable_audio_is_400(client, monkeypatch):
    class Broken:
        def transcribe(self, path, **kwargs):
            raise RuntimeError("Invalid data found when processing input")

    monkeypatch.setattr(speech, "_model", Broken())
    resp = await client.post("/transcribe", files={"file": ("a.wav", b"not audio")})
    assert resp.status_code == 400 and "Could not process" in resp.json()["detail"]


async def test_health(client, fake):
    body = (await client.get("/health")).json()
    assert body["status"] == "ok" and body["model"] == speech.MODEL and body["loaded"] is False
