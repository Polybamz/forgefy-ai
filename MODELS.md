# Model register

Every model this platform serves, with the license checked before it was used
in a paid product (plan §1, §5). Add a row before switching a model in
production, and pin the exact version: `docker compose exec ollama ollama list`
shows each model's ID (digest).

| Model | Ollama tag | ID (digest) | License | Commercial use | Used for | Added | Checked by |
|---|---|---|---|---|---|---|---|
| Qwen3 8B | `qwen3:8b` | *fill in after pull* | Apache-2.0 | Allowed | Default for both MVP agents | 2026-11-23 | *you* |

## How to check a license

1. Open the model's page on [ollama.com/library](https://ollama.com/library) and
   its source on [huggingface.co](https://huggingface.co/models); the license is
   on both.
2. Permissive (Apache-2.0, MIT): commercial use allowed — keep the notice.
3. Custom licenses (e.g. Llama, Gemma) usually allow commercial use with
   conditions — user caps, acceptable-use policies, attribution. Read them and
   note the conditions here.
4. Research / non-commercial licenses: not for Forgefy or Vndor.

## Candidates to benchmark next

Run `python -m app.bench` with each (set `DEFAULT_MODEL`), compare with the
reports in `benchmarks/`, and verify the license before adopting:

| Tag | Why |
|---|---|
| `qwen3:4b` | Faster; may be enough for Vndor product descriptions (`MODEL_OVERRIDES`) |
| `qwen3:14b` | Better requirements extraction; needs a GPU to be quick |
| `qwen3:30b` | Mixture of experts: much stronger, still CPU-usable — but needs ~20 GB, too much for a 24 GB server that also runs the platform |
