FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    WHISPER_CACHE=/models HF_HOME=/models/hf
WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Unprivileged user. /models is a volume: the downloaded Whisper model lives
# there and survives rebuilds. Created here so the volume starts owned by it.
RUN useradd --system --uid 10002 speech && mkdir -p /models && chown speech /models
USER speech

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
