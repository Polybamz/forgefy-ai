# AI platform (forgefy-models)

Our centralized AI infrastructure: open-weight models running on our own VPS,
behind one authenticated API that Forgefy, Vndor and future products share.
This folder is the **MVP** from the implementation plan (§12) — deliberately small.

```
 Forgefy ─┐                       ┌──────────────── private Docker network ────────────────┐
 Vndor ───┼─ HTTPS + API key ──►  Caddy :443 ──► API (FastAPI) ──► Ollama ──► qwen3:8b
 later …  ┘                       │                    │                                    │
                                  │                    └──► Postgres (keys, usage log)      │
                                  └─────────────────────────────────────────────────────────┘
```

| Service | Role | Public? |
|---|---|---|
| **caddy** | HTTPS with an automatic Let's Encrypt certificate | Yes — 80/443 only |
| **api** | Our gateway: API keys per application, rate limits, the agents, schema validation, usage log | Through Caddy |
| **ollama** | Runs the models | No |
| **postgres** | API keys (hashed) and the usage log | No |

Caddy fills the plan's "Nginx" role; it was chosen because it obtains and renews
certificates by itself.

## What the MVP includes

| Plan item | Here |
|---|---|
| One small open-weight model | `qwen3:8b` (Apache-2.0) by default — see [MODELS.md](MODELS.md) |
| One API with authentication and strict schemas | `api/` — every agent has a typed input and a JSON-schema output that the model is constrained to |
| Forgefy agent: requirements from a transcript segment | `POST /api/v1/agents/forgefy/requirements` |
| Vndor agent: factual product description | `POST /api/v1/agents/vndor/product-description` |
| Separate credentials per application | Keys belong to one app and environment; a Forgefy key cannot call Vndor agents |
| Usage log | Every call: app, task, model, status, latency, tokens — **never the content** |
| Benchmark before promising response times | `python -m app.bench` → `benchmarks/` |
| Hardening, backups, restore | `scripts/harden.sh`, `scripts/backup.sh`, `scripts/restore.sh` |

Beyond the plan's MVP, the same API serves **anything else** too: an
OpenAI-compatible `/v1` API with system instructions, your own tools,
streaming, **skills** and server-side **plugins** — see
[Using it from anything](#using-it-from-anything-the-openai-compatible-api).

**Deliberately not yet** (plan phases 5–7, add when there's a workload for them):
background jobs and job IDs (Redis), Prometheus/Grafana, LiteLLM routing,
LangGraph, external-model fallback, more agents.

### Guarding against made-up output

Models invent things. Each agent checks its answer against its input and
returns what it finds as `warnings` — it does not hide them:

- **Forgefy requirements:** each requirement carries a `source_quote`, and the
  API checks that the quote really appears in the transcript (`quote_verified`).
- **Vndor descriptions:** any number in the description that the merchant did
  not provide (an invented price, size, delivery time…) is flagged.

The calling application should show warnings to the user and require review
before saving — the plan's manual-review step.

## Server

The plan's starting point — a Contabo VPS with **8 vCPU, 24 GB RAM**, NVMe,
Ubuntu 24.04 — runs this MVP with `qwen3:8b`: about 16 GB for the model runtime
(`OLLAMA_MEMORY_LIMIT`) and 8 GB for the OS, Postgres and the API.

Expect CPU speeds of a few to ~10 tokens/s for an 8B model: a product
description in roughly 10–30 s, a requirements extraction in roughly 1–3
minutes. **Measure on the real server** with the benchmark before promising
response times. When that's too slow, the plan's next step is a separate GPU
host for the models (phase 7), keeping this API in front.

## Setup

**1. DNS.** Create an A record, e.g. `ai.yourdomain.com`, pointing at the VPS IP.

**2. Copy this folder to the server** (from your PC):

```powershell
scp -r C:\Users\USER\Desktop\forgefy\forgefy-models root@YOUR_VPS_IP:/opt/
```

**3. On the server:**

```bash
cd /opt/forgefy-models
chmod +x scripts/*.sh
curl -fsSL https://get.docker.com | sh          # if Docker isn't installed
sudo ./scripts/harden.sh                         # firewall, SSH keys only, auto-updates
./scripts/setup.sh ai.yourdomain.com             # .env, DB password, model sized to the RAM
docker compose up -d --build
./scripts/pull-models.sh                         # downloads qwen3:8b (~5 GB)
```

`harden.sh` turns SSH password login off **only if** an SSH key is already
installed, so it can't lock you out. Add yours first from your PC with
`ssh-copy-id root@YOUR_VPS_IP`, then re-run it.

**4. Create a key per application** — shown once, copy it into that app's secrets:

```bash
docker compose exec api python -m app.cli keys create --app forgefy --env production
docker compose exec api python -m app.cli keys create --app vndor --env production
```

Use separate keys for staging (`--env staging`).

**5. Test** end to end through HTTPS, from the server or your PC:

```bash
./scripts/test.sh https://ai.yourdomain.com <key>
```

```powershell
.\scripts\test.ps1 -Url https://ai.yourdomain.com -Key <key>
```

**6. Benchmark** and keep the report (plan phase 2's definition of done):

```bash
docker compose exec api python -m app.bench --runs 3
# also under load: --concurrent 2
```

**7. Backups** — schedule daily, and copy them off the server:

```bash
crontab -e
# 30 3 * * * cd /opt/forgefy-models && ./scripts/backup.sh >> backups/backup.log 2>&1
```

Back up `.env` too (it holds the database password). Practise a restore once:
`./scripts/restore.sh backups/<file>`.

## Using the API

Interactive docs with every schema: `https://ai.yourdomain.com/docs`.

```bash
curl https://ai.yourdomain.com/api/v1/agents/vndor/product-description \
  -H "Authorization: Bearer $VNDOR_KEY" -H "Content-Type: application/json" \
  -d '{"name": "Savon au karité", "facts": {"poids": "150 g"}, "language": "fr", "tone": "elegant"}'
```

Every agent answers with the same envelope:

```json
{
  "request_id": "…", "agent": "vndor.product-description", "agent_version": "2026-11-23.1",
  "model": "qwen3:8b", "latency_ms": 14210,
  "warnings": [],
  "usage": {"queue_ms": 0, "prompt_tokens": 180, "completion_tokens": 160, "tokens_per_second": 9.8, "attempts": 1},
  "result": {"short_description": "…", "long_description": "…", "highlights": ["…"]}
}
```

| Endpoint | |
|---|---|
| `POST /api/v1/agents/forgefy/requirements` | `{"transcript", "project_name"?, "language": "auto"\|"en"\|"fr"}` — up to `MAX_TRANSCRIPT_CHARS` (20,000); send longer meetings in segments |
| `POST /api/v1/agents/vndor/product-description` | `{"name", "category"?, "facts": {…}, "price"?, "notes"?, "language", "tone", "max_words"}` |
| `GET /api/v1/health` | Which app the key belongs to, runtime status, models, this app's agents |
| `GET /api/v1/usage?days=7` | This app's requests, latency and tokens by task and status |
| `GET /healthz` | Liveness, no key — for uptime monitors |

| Status | Meaning | What the caller should do |
|---|---|---|
| 401 | Missing, wrong or revoked key | Fix the key |
| 403 | Key belongs to another application | Use that app's key |
| 422 | Input invalid or too long | Fix the request (the detail says what) |
| 429 | Rate limit (per key, per minute) | Wait `Retry-After` seconds |
| 502 | The model's answer didn't match the schema, even after one retry | Retry once; if persistent, report it |
| 503 | Model busy (queue full) or unavailable | Wait `Retry-After` and retry |

Calls are processed one at a time on a CPU server (`OLLAMA_NUM_PARALLEL=1`);
others wait up to `QUEUE_TIMEOUT_SECONDS` for their turn, then get 503.

## Using it from anything: the OpenAI-compatible API

Besides the product agents, the platform offers a general chat API in OpenAI's
format at `https://ai.yourdomain.com/v1`. Anything that can talk to OpenAI —
the OpenAI SDKs, LangChain, LlamaIndex, n8n, Open WebUI, editor extensions,
your own scripts — can use your models by changing the base URL and key.

```python
from openai import OpenAI

client = OpenAI(base_url="https://ai.yourdomain.com/v1", api_key="aik_…")
reply = client.chat.completions.create(
    model="qwen3:8b",                      # or "default"
    messages=[
        {"role": "system", "content": "You are a concise assistant for a bakery."},
        {"role": "user", "content": "Suggest three names for a new croissant."},
    ],
)
print(reply.choices[0].message.content)
```

| Feature | How |
|---|---|
| System instructions | A `system` message, as usual |
| Your own tools | `tools=[…]` as usual. The model's `tool_calls` come back to you; run them and send the results as `tool` messages |
| Streaming | `stream=True` |
| JSON output | `response_format={"type": "json_object"}` or a JSON schema |
| Skills | `extra_body={"skills": ["name"]}` or `"all"` — see below |
| Plugins | `extra_body={"plugins": ["calculator", "clock"]}` — see below |

`GET /v1/models` lists the models on offer (`CHAT_MODELS`), `GET /v1/skills` the
skills and `GET /v1/plugins` the plugins your key may use.

**Qwen3 thinks before answering by default**, which on a CPU server roughly
doubles the time. Add `/no_think` to your message (or system prompt) when you
don't need it.

### Keys for other tools

```bash
# a key for scripts/integrations: chat + skills, no plugins
docker compose exec api python -m app.cli keys create --app scripts --env production
# with plugins
docker compose exec api python -m app.cli keys create --app n8n --env production --plugin calculator --plugin web
# change a key's permissions later (replaces them)
docker compose exec api python -m app.cli keys scopes aik_XXXXXXXX chat skills plugin:clock
```

Scopes: `agents` (Forgefy/Vndor agents — only for keys of those apps), `chat`,
`skills`, `plugin:<name>`, `plugin:*`.

## Skills

A skill is a folder of instructions the model reads **when a task needs it** —
the same `SKILL.md` format as [skills.sh](https://skills.sh) and Claude's Agent
Skills, so many published skills work here as they are.

```
skills/
  cameroon-business-writing/       ← included as an example
    SKILL.md                       ← name + description (frontmatter), then instructions
    examples.md                    ← extra files the instructions can point to
```

When a request enables skills, the model sees only each skill's name and
description, plus a `load_skill` tool. If the request matches a skill, it loads
the full instructions (and any extra file it needs), then answers. Many skills
can be on offer without filling the context window.

**Install a skill:** put its folder in `skills/`, then `docker compose restart api`.
**Write one:** see the example's `SKILL.md`. The `description` decides when the
model uses it — say what it's for and when to use it.

Skills are text the model follows, so treat a downloaded one like code: read it
before installing.

## Plugins

A plugin gives the model **tools that run on the server**: when the model calls
one, the platform runs it, gives the model the result, and continues until the
model answers. The steps taken are listed in the reply's `x_tool_steps`.

Included:

| Plugin | Tool | What it does |
|---|---|---|
| `calculator` | `calculator__calculate` | Exact arithmetic (small models get numbers wrong) — parsed safely, never `eval` |
| `clock` | `clock__now` | Current date and time in any time zone |
| `web` | `web__fetch` | Read a public web page. Refuses private and internal addresses, so it can't reach the database, the model runtime or cloud metadata. Grant it carefully |

### Writing a plugin

Copy `plugins/_template` to `plugins/<name>` (lowercase), and write functions:

```python
from typing import Annotated
from pydantic import Field
from app.plugins.sdk import tool

DESCRIPTION = "Order lookups for the shop"

@tool
async def order_status(order_id: Annotated[str, Field(description="e.g. VN-1042")]) -> dict:
    """Where an order is. Use when a customer asks about their order."""
    ...  # call your database or API here
    return {"order_id": order_id, "status": "shipped"}
```

- The **docstring** is what the model reads to decide when to call it.
- The **type hints** become the tool's JSON schema; arguments are validated
  before your function runs.
- Return text or anything JSON-serialisable; **raise** to report an error to the
  model. Sync or async both work. Calls time out after `TOOL_TIMEOUT_SECONDS`.
- Read secrets from environment variables, never from code or prompts.

Check it, then load it:

```bash
docker compose exec api python -m app.plugins.check plugins/<name>
docker compose exec api python -m app.plugins.check plugins/<name> order_status '{"order_id": "VN-1042"}'
docker compose restart api
docker compose exec api python -m app.cli keys scopes aik_XXXXXXXX chat skills plugin:<name>
```

**Plugins run inside the API with its permissions** — install only code you
have read. Callers can only *use* plugins: adding one needs access to the
server, and each key must be granted each plugin.

### How tools combine

- Your own tools (`tools=[…]`) always come back to you to run, plugins or not.
- With skills or plugins enabled, the server runs its own tool rounds (at most
  `MAX_TOOL_STEPS`, then the model must answer). If the model calls one of
  your tools in the same round as a server tool, only your calls are returned.
- With skills or plugins, `stream=True` sends the finished answer as a stream
  at the end, rather than token by token.

## Operating it

| Task | Command |
|---|---|
| Keys | `docker compose exec api python -m app.cli keys list` · `keys revoke aik_XXXXXXXX` |
| Usage, all apps | `docker compose exec api python -m app.cli usage --days 30` |
| Logs | `docker compose logs -f api` |
| What's in memory | `docker compose exec ollama ollama ps` |
| Update | `git pull` (or copy the folder), then `docker compose up -d --build` |
| Change model | Pull it, record it in [MODELS.md](MODELS.md), set `DEFAULT_MODEL` (or `MODEL_OVERRIDES` per agent), `docker compose up -d` |

**Rotating a key:** create a new one, put it in the application, deploy, then
revoke the old one.

**Adding an agent:** write a module in `api/app/agents/` like the two existing
ones (input model, output model, prompt, `finalize` checks), register it in
`api/app/agents/__init__.py`, add tests, and bump `version` whenever its prompt
or schema changes — the version is logged with every call.

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
- The usage log never contains prompts, transcripts or outputs. Applications
  should send only the fields a task needs (plan §9).
- `DOMAIN=:80` (plain HTTP) is for a first test without a domain only.
- Before processing health data (COA), do the separate assessment the plan calls for.
"# forgefy-ai" 
