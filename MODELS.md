# Model register

The models this server offers, why, and under what license. The plan (§1, §5)
asks for a license check before a model is used in a paid product, and for
exact versions to be pinned — `docker compose exec ollama ollama list` shows
each model's ID (digest); copy it into the table after pulling.

**Licenses and capabilities change between versions.** The notes below were
right when written; re-check the model's page before adopting it. What a pulled
model can actually do (tools, thinking, vision…) is reported live by
`GET /v1/models` and by the benchmark.

## Default set (8 vCPU / 24 GB CPU server)

| Role | Model (Ollama tag) | Family | Disk | License | Commercial use | Good for | ID (digest) |
|---|---|---|---|---|---|---|---|
| `general` (default) | `qwen3:8b` | Qwen (Alibaba) | ~5.2 GB | Apache-2.0 | Yes | All-round; tool calling; thinks before answering; strong multilingual | *after pull* |
| `fast` | `qwen3:4b` | Qwen | ~2.5 GB | Apache-2.0 | Yes | Quick, simple tasks: classification, short replies, tags | *after pull* |
| — | `mistral:7b` | Mistral (France) | ~4.4 GB | Apache-2.0 | Yes | Fast general model; worth testing for French | *after pull* |
| — | `llama3.1:8b` | Llama (Meta) | ~4.9 GB | Llama 3.1 Community License | Yes, with conditions¹ | Widely supported by tools; tool calling | *after pull* |
| `vision` | `gemma3:4b` | Gemma (Google) | ~3.3 GB | Gemma Terms of Use | Yes, with conditions² | Reads images (product photos, screenshots, documents) | *after pull* |
| `reasoning` | `deepseek-r1:8b` | DeepSeek | ~5.2 GB | MIT | Yes | Step-by-step reasoning: maths, planning, logic. Slow on CPU — it thinks at length | *after pull* |
| `embed` | `bge-m3` | BGE (BAAI) | ~1.2 GB | MIT | Yes | Embeddings for search and similarity, multilingual incl. French | *after pull* |

¹ Llama 3.1: commercial use allowed; services with over 700 million monthly
users need a separate license from Meta; display "Built with Llama"; follow
Meta's acceptable-use policy.
² Gemma: commercial use allowed; follow Google's prohibited-use policy and pass
the terms on to anyone you redistribute the model to.

`setup.sh` picks this set on a 24 GB server. On bigger servers it moves up a
size (`qwen3:14b`, `mistral-nemo:12b`, `gemma3:12b`, `deepseek-r1:14b`); on
small ones, down (`qwen3:4b`, `qwen3:1.7b`, `deepseek-r1:1.5b`).

## Roles

Apps can ask for a model by name (`"model": "mistral:7b"`) or by **role**
(`"model": "fast"`). Roles are defined in `MODEL_ALIASES` in `.env`:

```env
MODEL_ALIASES=general=qwen3:8b,fast=qwen3:4b,reasoning=deepseek-r1:8b,vision=gemma3:4b,embed=bge-m3,claude-*=qwen3:8b
```

Asking by role is better for apps: when the benchmark shows another model does
a job better, change the role here and every app follows, with no code change.

## Memory: why only two at once

On 24 GB, `OLLAMA_MEMORY_LIMIT=16g` and `OLLAMA_MAX_LOADED_MODELS=2`: two
~5 GB models stay loaded, with room for their working memory. Asking for a
third loads it (10–60 s on a CPU) and unloads the least recently used. So:

- Keep the roles your apps use most on models that are often loaded together.
- Prefer roles over many different model names in one app.
- The embedding model is small and can usually stay loaded alongside.

## Choosing, with evidence

```bash
docker compose exec api python -m app.bench                      # every model on offer
docker compose exec api python -m app.bench --models mistral:7b,llama3.1:8b
```

The report (in `benchmarks/`) shows each model's capabilities, speed (tokens/s),
latency for short and long answers, French, and whether it actually calls a
tool when it should. Use it to decide which model fills which role.

## Adding a model

1. Find it on [ollama.com/library](https://ollama.com/library) (or Hugging Face) and
   **check its license** (see below).
2. Add its tag to `MODELS` and to `CHAT_MODELS` (or `EMBEDDING_MODELS`) in `.env`.
3. `./scripts/pull-models.sh <tag>`, then `docker compose up -d`.
4. Benchmark it, and add a row above.

### How to check a license

1. The license is on the model's Ollama page and its Hugging Face page.
2. Permissive (Apache-2.0, MIT): commercial use allowed — keep the notice.
3. Custom licenses (Llama, Gemma and others): usually commercial use with
   conditions — read them and note the conditions here.
4. Research / non-commercial licenses: not for paid products.
