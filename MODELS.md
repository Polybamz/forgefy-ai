# Model register

The models this server offers, why, and under what license. The plan (§1, §5)
asks for a license check before a model is used in a paid product, and for
exact versions to be pinned — `docker compose exec ollama ollama list` shows
each model's ID (digest); copy it into the table after pulling.

**Licenses and capabilities change between versions.** The notes below were
right when written; re-check the model's page before adopting it. What a pulled
model can actually do (tools, thinking, vision…) is reported live on the
console's Models page, by `GET /v1/models`, and by the benchmark.

## Default set — 8 vCPU / 24 GB CPU server

| Role | Model (Ollama tag) | Family | Disk | License | Commercial use | Good for |
|---|---|---|---|---|---|---|
| `general` (default) | `qwen3:8b` | Qwen (Alibaba) | ~5.2 GB | Apache-2.0 | Yes | All-round assistant; tool calling; multilingual |
| `fast` | `qwen3:4b` | Qwen | ~2.5 GB | Apache-2.0 | Yes | Quick, simple tasks: classification, short replies, tags |
| `code` | `qwen2.5-coder:7b` | Qwen | ~4.7 GB | Apache-2.0 | Yes | Writing, fixing and explaining code |
| `reasoning` | `deepseek-r1:8b` | DeepSeek | ~5.2 GB | MIT | Yes | Step-by-step reasoning: maths, planning, logic. Slow on CPU — it thinks at length |
| `vision` | `gemma3:4b` | Gemma (Google) | ~3.3 GB | Gemma Terms of Use | Yes, with conditions² | Reads images (product photos, screenshots, documents) |
| `embed` | `bge-m3` | BGE (BAAI) | ~1.2 GB | MIT | Yes | Embeddings for search and similarity, multilingual incl. French |
About 22 GB on disk (plus ~1.6 GB for Whisper). Two models stay in memory at a time (see below).

**Optional, not downloaded by default.** On a 24 GB CPU server these mostly
duplicate what qwen3 already does, and every extra model an app picks costs a
10-60 s reload. Add one to `MODELS` and `CHAT_MODELS` if an app needs it:

| Model (Ollama tag) | Family | Disk | License | Commercial use | Good for |
|---|---|---|---|---|---|
| `mistral:7b` | Mistral (France) | ~4.4 GB | Apache-2.0 | Yes | Fast general model; worth testing for French |
| `llama3.1:8b` | Llama (Meta) | ~4.9 GB | Llama 3.1 Community License | Yes, with conditions¹ | Widely supported by tools; tool calling |
| `phi4-mini:3.8b` | Phi (Microsoft) | ~2.5 GB | MIT | Yes | Lightweight; good reasoning for its size |

¹ Llama 3.1: commercial use allowed; services with over 700 million monthly
users need a separate license from Meta; display "Built with Llama"; follow
Meta's acceptable-use policy.
² Gemma: commercial use allowed; follow Google's prohibited-use policy and pass
the terms on to anyone you redistribute the model to.

## Bigger models — for a bigger server or a GPU

These are excellent, but at 9–19 GB each they don't fit beside a second model
and the platform's own services in 24 GB. `setup.sh` offers them automatically
when the server has room:

| Model (Ollama tag) | Disk | License | Good for | Offered from |
|---|---|---|---|---|
| `qwen3:14b` | ~9.3 GB | Apache-2.0 | Stronger `general` | 32 GB RAM / 20 GB VRAM |
| `qwen2.5-coder:14b` | ~9 GB | Apache-2.0 | Stronger `code` | 32 GB RAM / 20 GB VRAM |
| `deepseek-r1:14b` | ~9 GB | MIT | Stronger `reasoning` | 32 GB RAM / 20 GB VRAM |
| `gemma3:12b` | ~8.1 GB | Gemma Terms | Stronger `vision` | 32 GB RAM / 20 GB VRAM |
| `phi4:14b` | ~9.1 GB | MIT | Strong reasoning for its size | 32 GB RAM / 20 GB VRAM |
| `mistral-nemo:12b` | ~7.1 GB | Apache-2.0 | Long context, multilingual | 32 GB RAM / 20 GB VRAM |
| `qwen3-coder:30b` | ~19 GB | Apache-2.0 | Coding agent; mixture-of-experts, so fast for its size | 50 GB RAM / 40 GB VRAM |
| `devstral:24b` | ~14 GB | Apache-2.0 | Agentic coding (multi-step edits, tool use) | 50 GB RAM / 40 GB VRAM |
| `gpt-oss:20b` | ~14 GB | Apache-2.0 | General assistant with tool calling and adjustable reasoning | 50 GB RAM / 40 GB VRAM |

On a 24 GB server you *can* still add one of these by hand (`MODELS`,
`CHAT_MODELS`, `./scripts/pull-models.sh`) and set `OLLAMA_MAX_LOADED_MODELS=1`,
so it loads alone — but every switch between models then costs a reload.

## Not language models: speech and images

Two models on the recommended list are a different kind of model, which
Ollama doesn't run:

| Need | Model | License | Status |
|---|---|---|---|
| Speech → text | **Whisper large-v3-turbo** (`WHISPER_MODEL`) | MIT | **Built in** — the `speech` service, behind `/v1/audio/transcriptions`. About real time on 4 CPU cores: fine for uploaded recordings, too slow for live meetings. `large-v3` is a little more accurate and several times slower; `medium`/`small` are faster and weaker. Downloads from Hugging Face on first use. |
| Text → image | **FLUX.1-schnell** | Apache-2.0 | Needs a GPU. On CPU a single image takes many minutes and ~24 GB RAM — not practical on this server. Plan it for the GPU phase (plan §11, phase 7). |

## Roles

Apps can ask for a model by name (`"model": "mistral:7b"`) or by **role**
(`"model": "code"`). Roles are defined in `MODEL_ALIASES` in `.env`:

```env
MODEL_ALIASES=general=qwen3:8b,fast=qwen3:4b,code=qwen2.5-coder:7b,reasoning=deepseek-r1:8b,vision=gemma3:4b,embed=bge-m3,claude-*=qwen3:8b
```

Asking by role is better for apps: when the benchmark shows another model does
a job better, change the role here and every app follows, with no code change.

## Memory: why only two at once

On 24 GB, `OLLAMA_MEMORY_LIMIT=14g` and `OLLAMA_MAX_LOADED_MODELS=2`: two
~5 GB models stay loaded, with room for their working memory. Asking for a
third loads it (10–60 s on a CPU) and unloads the least recently used. So:

- Keep the roles your apps use most on models that are often loaded together.
- Prefer roles over many different model names in one app.
- The embedding model is small and can usually stay loaded alongside.

## Choosing, with evidence

```bash
docker compose exec api python -m app.bench                      # every model on offer
docker compose exec api python -m app.bench --models qwen2.5-coder:7b,phi4-mini:3.8b
```

The report (in `benchmarks/`) shows each model's capabilities, speed (tokens/s),
latency for short and long answers, French, code, and whether it actually calls
a tool when it should. Use it to decide which model fills which role.

## Where models come from

| Source | Use it for |
|---|---|
| [Ollama library](https://ollama.com/library) | The default: `ollama pull <tag>`, already quantised to run on CPU |
| [Hugging Face](https://huggingface.co/models) | Anything else. Ollama can pull GGUF files straight from it: `hf.co/<user>/<repo>:<quant>` as the model name |
| Unsloth | Ready-made GGUF quantisations on Hugging Face, and tools for fine-tuning a model on your own data |
| vLLM | A faster server than Ollama for many users at once — on a GPU. A later option for the GPU phase; this API can sit in front of it the same way |
| llama.cpp / LM Studio | The engine under Ollama / a desktop app — for trying models on your own PC |

## Adding a model

1. Find it on the Ollama library or Hugging Face, and **check its license** (below).
2. Add its tag to `MODELS` and to `CHAT_MODELS` (or `EMBEDDING_MODELS`) in `.env`.
3. `./scripts/pull-models.sh <tag>`, then `docker compose up -d`.
4. Benchmark it, and add a row above.

### How to check a license

1. The license is on the model's Ollama page and its Hugging Face page.
2. Permissive (Apache-2.0, MIT): commercial use allowed — keep the notice.
3. Custom licenses (Llama, Gemma and others): usually commercial use with
   conditions — read them and note the conditions here.
4. Research / non-commercial licenses: not for paid products.
