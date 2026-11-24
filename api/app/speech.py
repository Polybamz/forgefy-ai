"""Speech-to-text, OpenAI-compatible, in front of the private speech service.

    POST /v1/audio/transcriptions   audio → text in the language spoken
    POST /v1/audio/translations     audio in any language → English text

Multipart form, as OpenAI's API: file, model (accepted and ignored — the
server has one speech model, see WHISPER_MODEL), language, prompt,
response_format (json | text | verbose_json | srt | vtt), temperature.
The OpenAI SDKs' client.audio.transcriptions.create(...) works unchanged.

Each request is logged with the length of the audio, never its content.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse

from app.auth import authenticate
from app.llm import ModelBusy
from app.store import ApiKey, UsageRecord

logger = logging.getLogger("ai-api")
Caller = Annotated[ApiKey, Depends(authenticate)]
FORMATS = ("json", "text", "verbose_json", "srt", "vtt")


class SpeechError(Exception):
    def __init__(self, message: str, status: int = 503) -> None:
        super().__init__(message)
        self.status = status


class SpeechClient:
    """Talks to the speech service, one recording at a time (it uses every core it has)."""

    def __init__(self, base_url: str, *, timeout: float, queue_timeout: float,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._sem = asyncio.Semaphore(1)
        self._queue_timeout = queue_timeout
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"),
                                         timeout=httpx.Timeout(timeout, connect=10), transport=transport)

    async def close(self) -> None:
        await self._client.aclose()

    async def transcribe(self, filename: str, content: bytes, content_type: str, *, task: str,
                         language: str | None, prompt: str | None, temperature: float) -> dict[str, Any]:
        try:
            await asyncio.wait_for(self._sem.acquire(), self._queue_timeout)
        except TimeoutError as exc:
            raise ModelBusy from exc
        try:
            data = {"task": task, "temperature": str(temperature)}
            if language:
                data["language"] = language
            if prompt:
                data["prompt"] = prompt
            resp = await self._client.post("/transcribe", data=data,
                                           files={"file": (filename, content, content_type)})
        except httpx.TimeoutException as exc:
            raise SpeechError("transcription took too long — send shorter recordings") from exc
        except httpx.HTTPError as exc:
            raise SpeechError(f"speech service unreachable: {exc}") from exc
        finally:
            self._sem.release()
        if resp.is_error:
            try:
                detail = resp.json().get("detail") or resp.text
            except ValueError:
                detail = resp.text
            raise SpeechError(str(detail)[:300], 400 if resp.status_code in (400, 413, 422) else 503)
        return resp.json()

    async def health(self) -> dict[str, Any]:
        try:
            resp = await self._client.get("/health", timeout=5)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            return {"status": f"unreachable: {exc}"}


router = APIRouter(prefix="/v1/audio", tags=["speech (OpenAI compatible)"])


@router.post("/transcriptions")
async def transcriptions(
    caller: Caller, request: Request,
    file: UploadFile = File(...),
    model: str = Form("whisper-1"),
    language: str | None = Form(None),
    prompt: str | None = Form(None),
    response_format: str = Form("json"),
    temperature: float = Form(0.0),
):
    """Audio → text, in the language spoken (or `language`, e.g. "fr", to skip detection)."""
    return await _run(caller, request, file, "transcribe", language, prompt, response_format, temperature)


@router.post("/translations")
async def translations(
    caller: Caller, request: Request,
    file: UploadFile = File(...),
    model: str = Form("whisper-1"),
    prompt: str | None = Form(None),
    response_format: str = Form("json"),
    temperature: float = Form(0.0),
):
    """Audio in any language → English text."""
    return await _run(caller, request, file, "translate", None, prompt, response_format, temperature)


async def _run(caller: ApiKey, request: Request, file: UploadFile, task: str, language: str | None,
               prompt: str | None, response_format: str, temperature: float):
    if not caller.allows("chat"):
        raise HTTPException(403, "This key does not have the 'chat' permission.")
    if response_format not in FORMATS:
        raise HTTPException(422, f"response_format must be one of: {', '.join(FORMATS)}")
    state = request.app.state
    limit = state.settings.MAX_AUDIO_MB * 1024 * 1024
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(413, f"Audio file is over {state.settings.MAX_AUDIO_MB} MB. Split it, or compress it "
                                 "(e.g. 64 kbit/s mono MP3 holds about 2 hours in 60 MB).")
    if not content:
        raise HTTPException(422, "The audio file is empty.")

    record = UsageRecord(
        request_id=request.headers.get("x-request-id") or uuid.uuid4().hex, key_id=caller.id,
        app=caller.app, environment=caller.environment,
        task="transcription" if task == "transcribe" else "translation",
        model=state.settings.WHISPER_MODEL, status="ok", latency_ms=0,
    )
    started = time.monotonic()
    try:
        result = await state.speech.transcribe(
            file.filename or "audio", content, file.content_type or "application/octet-stream",
            task=task, language=language, prompt=prompt, temperature=temperature)
        record.model = result.get("model") or record.model
        record.audio_seconds = result.get("duration")
    except ModelBusy as exc:
        record.status, record.error = "busy", "queue timeout"
        raise HTTPException(503, "The speech service is busy; retry shortly.", headers={"Retry-After": "60"}) from exc
    except SpeechError as exc:
        record.status, record.error = ("bad_request" if exc.status == 400 else "model_error"), str(exc)
        raise HTTPException(exc.status, str(exc)) from exc
    finally:
        record.latency_ms = int((time.monotonic() - started) * 1000)
        try:
            await state.store.log_usage(record)
        except Exception:
            logger.exception("could not record usage request_id=%s", record.request_id)
        logger.info("%s app=%s audio_s=%s status=%s latency_ms=%d", record.task, caller.app,
                    record.audio_seconds, record.status, record.latency_ms)

    return _format(result, task, response_format)


def _format(result: dict[str, Any], task: str, response_format: str):
    if response_format == "json":
        return {"text": result["text"]}
    if response_format == "text":
        return PlainTextResponse(result["text"])
    if response_format == "verbose_json":
        return {"task": "transcribe" if task == "transcribe" else "translate",
                "language": result.get("language"), "duration": result.get("duration"),
                "text": result["text"], "segments": result.get("segments", [])}
    sep = "," if response_format == "srt" else "."
    blocks = [f"{_timestamp(s['start'], sep)} --> {_timestamp(s['end'], sep)}\n{s['text']}"
              for s in result.get("segments", [])]
    if response_format == "srt":
        body = "\n\n".join(f"{i}\n{b}" for i, b in enumerate(blocks, 1))
    else:
        body = "WEBVTT\n\n" + "\n\n".join(blocks)
    return PlainTextResponse(body + "\n", media_type="text/vtt" if response_format == "vtt" else "text/plain")


def _timestamp(seconds: float, sep: str) -> str:
    ms = int(round(seconds * 1000))
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h:02}:{m:02}:{s:02}{sep}{ms:03}"
