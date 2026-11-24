"""Speech-to-text for the model server: Whisper, through faster-whisper.

A private service — only the API gateway calls it, after checking the caller's
key. One recording is transcribed at a time: transcription keeps every CPU
core it is given busy, so running two at once only makes both slower.

    POST /transcribe   multipart: file, task (transcribe | translate), language,
                       prompt, temperature → text, language, duration, segments
    GET  /health       model, whether it's loaded, whether it's busy

The model downloads from Hugging Face on first use (~1.6 GB for
large-v3-turbo) into a volume, then loads from there.
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s speech %(message)s")
logger = logging.getLogger("speech")

# large-v3-turbo: close to large-v3's accuracy, several times faster — the
# sensible choice on a CPU. Others: large-v3, medium, small, base, tiny.
MODEL = os.environ.get("WHISPER_MODEL", "large-v3-turbo")
DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")              # cpu | cuda | auto
COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")  # int8 on CPU; float16 on a GPU
THREADS = int(os.environ.get("WHISPER_THREADS", "0"))           # 0 = every core the container gets
# 1 = greedy decoding, fastest. 5 is a little more accurate and ~2x slower on CPU.
BEAM_SIZE = int(os.environ.get("WHISPER_BEAM_SIZE", "1"))
CACHE_DIR = os.environ.get("WHISPER_CACHE", "/models")
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "100")) * 1024 * 1024

app = FastAPI(title="Speech", docs_url=None, redoc_url=None)
_model: Any = None
_load_lock = asyncio.Lock()
_busy = asyncio.Lock()


def _load_model() -> Any:
    from faster_whisper import WhisperModel

    logger.info("loading %s on %s (%s)", MODEL, DEVICE, COMPUTE_TYPE)
    return WhisperModel(MODEL, device=DEVICE, compute_type=COMPUTE_TYPE, cpu_threads=THREADS,
                        download_root=CACHE_DIR)


async def _get_model() -> Any:
    global _model
    async with _load_lock:
        if _model is None:
            _model = await asyncio.to_thread(_load_model)
    return _model


def _transcribe(model: Any, path: str, task: str, language: str | None, prompt: str | None,
                temperature: float) -> dict[str, Any]:
    segments, info = model.transcribe(
        path, task=task, language=language or None, initial_prompt=prompt or None,
        temperature=temperature, beam_size=BEAM_SIZE,
        # Skip silence: faster on meetings with pauses, and avoids Whisper
        # inventing words in quiet stretches.
        vad_filter=True,
    )
    out = [{"id": i, "start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
           for i, s in enumerate(segments)]
    return {
        "text": " ".join(s["text"] for s in out).strip(),
        "language": info.language,
        "language_probability": round(info.language_probability, 3),
        "duration": round(info.duration, 2),
        "segments": out,
    }


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "model": MODEL, "device": DEVICE, "loaded": _model is not None,
            "busy": _busy.locked()}


@app.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    task: str = Form("transcribe"),
    language: str | None = Form(None),
    prompt: str | None = Form(None),
    temperature: float = Form(0.0),
) -> dict[str, Any]:
    if task not in ("transcribe", "translate"):
        raise HTTPException(422, "task must be transcribe or translate")

    # Save the upload to a temporary file (the decoder reads from disk), with a size cap.
    suffix = Path(file.filename or "audio").suffix[:10]
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        size = 0
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                tmp.close()
                os.unlink(tmp.name)
                raise HTTPException(413, f"Audio file is over {MAX_UPLOAD_BYTES // 1024 // 1024} MB.")
            tmp.write(chunk)
    try:
        model = await _get_model()
        async with _busy:
            started = time.monotonic()
            try:
                result = await asyncio.to_thread(_transcribe, model, tmp.name, task, language, prompt, temperature)
            except Exception as exc:  # undecodable or corrupt audio, mostly
                logger.warning("transcription failed: %s", exc)
                raise HTTPException(400, f"Could not process the audio: {exc}") from exc
            took = time.monotonic() - started
    finally:
        os.unlink(tmp.name)
    logger.info("%s %.1fs of audio in %.1fs (%s)", task, result["duration"], took, result["language"])
    return {**result, "model": MODEL, "processing_seconds": round(took, 2)}
