# Self-hosted models (forgefy-models)

Free, open-weight AI models running on your own VPS, behind API keys, for your
apps — Forgefy, Vndor, anything else — and for anyone you give a key to.

```
 Your apps ─┐                     ┌──────────── private Docker network ─────────────────┐
 Customers ─┼─ HTTPS + API key ─► Caddy :443 ─► API ─► Ollama ─► Qwen · Mistral · Llama
 Tools ─────┘                     │              │               Gemma · DeepSeek · BGE │
                                  │              └─► Postgres (keys, usage log)          │
                                  └──────────────────────────────────────────────────────┘
```

## Models

Several families, so each kind of task can use the model that suits it — see
[MODELS.md](MODELS.md) for licenses and details:

| Role | Default model (24 GB server) | For |
|---|---|---|
| `general` | `qwen3:8b` | Most things; tool calling |
| `fast` | `qwen3:4b` | Quick, simple tasks |
| `code` | `qwen2.5-coder:7b` | Writing, fixing and explaining code |
| `reasoning` | `deepseek-r1:8b` | Maths, planning, step-by-step logic |
| `vision` | `gemma3:4b` | Reading images |
| `embed` | `bge-m3` | Embeddings for search and similarity |

Apps can ask for a model by name (`"model": "qwen3:8b"`) or by role
(`"model": "fast"`). Roles are set in `MODEL_ALIASES`; change which model fills
a role after benchmarking, and every app follows. Any other model from
[ollama.com/library](https://ollama.com/library) or Hugging Face can be added.

On a bigger server, `setup.sh` steps up automatically — 14B models from 32 GB
RAM, and from 50 GB RAM (or a 40 GB GPU) the big coding and assistant models:
Qwen3-Coder 30B, Devstral and gpt-oss-20b. **Speech-to-text** (Whisper) runs as
its own service — see [Speech-to-text](#speech-to-text). Image generation (FLUX)
needs a GPU; see [MODELS.md](MODELS.md).

On 24 GB, two models stay in memory at once; asking for another loads it
(10–60 s on a CPU) and unloads the least recently used.

| Service | Role | Public? |
|---|---|---|
| **caddy** | HTTPS with an automatic Let's Encrypt certificate | Yes — 80/443 only |
| **api** | The gateway: API keys, rate limits, a queue in front of the model, usage log | Through Caddy |
| **ollama** | Runs the models | No |
| **speech** | Speech-to-text (Whisper, through faster-whisper) | No |
| **postgres** | API keys (hashed) and the usage log | No |

Ollama has no authentication of its own, so it is never exposed: everything
goes through the API, which checks the key, applies the rate limit, queues the
request and records usage — then passes the request to the model unchanged.

## What you can call

Your models, in the three formats AI clients speak:

| Format | Endpoint | Use it from |
|---|---|---|
| **OpenAI** | `POST /v1/chat/completions` | OpenAI SDKs, LangChain, LlamaIndex, n8n, Open WebUI, Continue, Cline — anything with an "OpenAI-compatible endpoint" setting |
| **Anthropic** | `POST /v1/messages` | Claude Code, the Claude Agent SDK, Anthropic SDKs |
| **Ollama** | `POST /api/chat` | Forgefy's backend (it already speaks this), and any Ollama client |

Everything a client sends goes through as-is: **system instructions, its own
tools and tool results, streaming, JSON output**. So the client keeps full
control — your app writes its own prompts, and tools like Claude Code keep
their skills, plugins and MCP servers on their side.

**Embeddings** (text → vectors, for search, recommendations, duplicate
detection): `POST /v1/embeddings` (OpenAI format) or `POST /api/embed` (Ollama
format), with `"model": "embed"`.

Also: `GET /v1/models` and `GET /api/tags` (models and roles on offer, with what
each can do — tools, thinking, vision), `POST /v1/messages/count_tokens` (an
estimate), `GET /api/v1/health`, `GET /api/v1/usage`, `GET /healthz` (no key,
for uptime monitors), and interactive docs at `/docs`.

## Speech-to-text

Whisper turns recordings into text, in the language spoken (English, French
and ~100 others) — or translates them into English. Same keys, OpenAI's format:

```python
from openai import OpenAI

ai = OpenAI(base_url="https://ai.yourdomain.com/v1", api_key="aik_…", timeout=1800)
with open("meeting.mp3", "rb") as f:
    result = ai.audio.transcriptions.create(model="whisper-1", file=f, language="fr")
print(result.text)
```

```bash
curl https://ai.yourdomain.com/v1/audio/transcriptions -H "Authorization: Bearer aik_…" \
  -F file=@meeting.mp3 -F response_format=verbose_json
```

| Endpoint | |
|---|---|
| `POST /v1/audio/transcriptions` | Audio → text. `language` ("en", "fr"…) skips detection; `prompt` can list names and jargon to spell right |
| `POST /v1/audio/translations` | Audio in any language → English text |

`response_format`: `json` (`{"text"}`), `text`, `verbose_json` (language,
duration, timed segments), `srt` or `vtt` (subtitles). `model` is accepted for
compatibility; the server always uses `WHISPER_MODEL` (default
`large-v3-turbo`). Files: mp3, wav, m4a, ogg, webm, flac… up to `MAX_AUDIO_MB`
(100 MB).

**Speed on a CPU:** roughly real time on 4 cores — a 10-minute recording takes
about 10 minutes, and recordings are transcribed one at a time. Fine for
uploaded recordings; for live meetings keep a streaming service (Deepgram)
or move speech to a GPU. Requests time out after 30 minutes, so split long
recordings into parts of ~20 minutes. Usage is recorded in minutes of audio.

## Server

A Contabo VPS with **8 vCPU, 24 GB RAM**, NVMe, Ubuntu 24.04 runs the default
set above: about 14 GB for the model runtime (`OLLAMA_MEMORY_LIMIT`, two models
loaded at a time), 3 GB for speech-to-text, and the rest for the OS, Postgres
and the API. The models take ~39 GB of disk (37 GB language models, 1.6 GB
Whisper).

On a CPU, expect a few to ~10 tokens/s from an 8B model — a short answer in
seconds, a long one in a minute or two. **Measure on the real server** with the
benchmark before promising response times. When that's too slow, move the
models to a GPU server and keep this API in front.

## Setup

**1. DNS.** Create an A record, e.g. `ai.yourdomain.com`, pointing at the VPS IP.

**2. Copy this folder to the server** (from your PC):

```powershell
scp -r C:\Users\USER\Desktop\forgefy\forgefy-models root@YOUR_VPS_IP:/opt/
```

Then, on the server, remove what belongs to your PC only — its `.env` (local
settings) and the local HTTPS-certificate fix — so `setup.sh` writes the
server's own:

```bash
cd /opt/forgefy-models && rm -rf .env docker-compose.override.yml local-ca
```

(Or push the folder to a private git repo and `git clone` it on the server —
git already leaves those files out.)

**3. On the server:**

```bash
cd /opt/forgefy-models
chmod +x scripts/*.sh
curl -fsSL https://get.docker.com | sh          # if Docker isn't installed
sudo ./scripts/harden.sh                         # firewall, SSH keys only, auto-updates
./scripts/setup.sh ai.yourdomain.com             # .env, DB password, model sized to the RAM
docker compose up -d --build
./scripts/pull-models.sh                         # downloads the models (~37 GB)
```

`harden.sh` turns SSH password login off **only if** an SSH key is already
installed, so it can't lock you out. Add yours first from your PC with
`ssh-copy-id root@YOUR_VPS_IP`, then re-run it.

**4. Create a key for each app** (or customer) — shown once, copy it into that app's secrets:

```bash
docker compose exec api python -m app.cli keys create --app forgefy --env production
docker compose exec api python -m app.cli keys create --app vndor --env production
```

**5. Test** end to end through HTTPS, from the server or your PC:

```bash
./scripts/test.sh https://ai.yourdomain.com <key>
```

```powershell
.\scripts\test.ps1 -Url https://ai.yourdomain.com -Key <key>
```

**6. Compare the models** and keep the report — then set which model fills
each role (`MODEL_ALIASES` in `.env`, then `docker compose up -d`):

```bash
docker compose exec api python -m app.bench          # every model; takes a while on CPU
```

**7. Backups** — schedule daily, and copy them off the server:

```bash
crontab -e
# 30 3 * * * cd /opt/forgefy-models && ./scripts/backup.sh >> backups/backup.log 2>&1
```

Back up `.env` too (it holds the database password). Practise a restore once:
`./scripts/restore.sh backups/<file>`.

## Using it from your app

**Forgefy's backend** already talks to Ollama's API, so it needs settings, not
code — point it at this server with its key:

```env
OLLAMA_URL=https://ai.yourdomain.com
OLLAMA_API_KEY=aik_…          # a key made with --app forgefy
OLLAMA_MODEL=qwen3:8b
BP_MODEL=Qwen3                # and/or BUILD_MODEL=Qwen3
```

**Any app, OpenAI style** (Python shown; every language has an OpenAI SDK):

```python
from openai import OpenAI

client = OpenAI(base_url="https://ai.yourdomain.com/v1", api_key="aik_…")
reply = client.chat.completions.create(
    model="qwen3:8b",
    messages=[
        {"role": "system", "content": "You write product descriptions for small shops."},
        {"role": "user", "content": "Raffia tote bag, natural raffia, 40 x 30 cm."},
    ],
)
print(reply.choices[0].message.content)
```

**Claude Code** on your model:

```bash
export ANTHROPIC_BASE_URL=https://ai.yourdomain.com
export ANTHROPIC_API_KEY=aik_…
claude
```

Claude Code asks for `claude-…` model names; `MODEL_ALIASES` (set by
`setup.sh` to `claude-*=<your model>`) runs them on yours. It also sends very
large prompts — raise `NUM_CTX` to 32768 for it (costs RAM).

**Tools with a settings screen** (Open WebUI, n8n, Continue…): provider
"OpenAI-compatible", base URL `https://ai.yourdomain.com/v1`, the key, model
`qwen3:8b`.

### Things to know

- **Models on offer** are those in `CHAT_MODELS` and `EMBEDDING_MODELS`, plus
  the role names in `MODEL_ALIASES`. A request for anything else gets a clear
  error listing what is available.
- **Not every model can do everything.** Gemma can read images but (in Ollama)
  not call tools; DeepSeek-R1 always reasons at length; Mistral and Llama don't
  have a thinking mode. `GET /v1/models` lists each model's capabilities, so an
  app can check before relying on one.
- **Thinking vs speed.** Qwen3 thinks before answering, which roughly doubles
  response time on a CPU — but without it, small models call tools far less
  reliably and make more factual mistakes. The default (`THINKING=model`)
  leaves it on. Clients can turn it off per request: `reasoning_effort: "none"`
  (OpenAI), `thinking: {"type": "disabled"}` (Anthropic), `think: false` (Ollama).
- **One request at a time** on a CPU server (`OLLAMA_NUM_PARALLEL=1`); others
  wait up to `QUEUE_TIMEOUT_SECONDS`, then get `503` with `Retry-After`.

| Status | Meaning | What the caller should do |
|---|---|---|
| 401 | Missing, wrong or revoked key | Fix the key |
| 422 | Bad request, or a model that isn't offered (the message lists what is) | Fix the request |
| 429 | Rate limit (per key, per minute) | Wait `Retry-After` seconds |
| 503 | Model busy (queue full) or unavailable | Wait `Retry-After` and retry |

## Operating it

| Task | Command |
|---|---|
| Keys | `docker compose exec api python -m app.cli keys list` · `keys revoke aik_XXXXXXXX` |
| Usage per app | `docker compose exec api python -m app.cli usage --days 30` |
| Logs | `docker compose logs -f api` |
| What's in memory | `docker compose exec ollama ollama ps` |
| Add a model | Check its license, add it to `MODELS` and `CHAT_MODELS` in `.env`, `./scripts/pull-models.sh`, record it in [MODELS.md](MODELS.md), `docker compose up -d` |
| Change a role | Edit `MODEL_ALIASES` in `.env` (e.g. `fast=qwen3:8b`), `docker compose up -d` |
| Update | Copy the folder (or `git pull`), then `docker compose up -d --build` |

**Rotating a key:** create a new one, put it in the app, deploy, then revoke the old one.

## Development

```bash
cd api
pip install -r requirements-dev.txt
python -m pytest
```

Or without a local Python:
`docker run --rm -v "$PWD/api:/srv" -w /srv python:3.12-slim sh -c "pip install -q -r requirements-dev.txt && pytest"`.

The tests use an in-memory store and a fake model, so they need neither
Postgres nor Ollama.

## Security notes

- Only Caddy publishes ports. Docker bypasses `ufw` for published ports, so
  never add `ports:` to `ollama`, `postgres` or `api`.
- Keys are stored as SHA-256 hashes; a lost key cannot be recovered, only replaced.
- The usage log never contains prompts or answers — only who, when, which
  model, how long, how many tokens.
- `DOMAIN=:80` (plain HTTP) is for a first test without a domain only.
- Check each model's license before using it in a paid product ([MODELS.md](MODELS.md)).
"# forgefy-ai"
