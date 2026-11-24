"""Compare the models on this server: what each can do, and how fast.

    docker compose exec api python -m app.bench                          # every model on offer
    docker compose exec api python -m app.bench --models qwen3:8b,mistral:7b --runs 3
    docker compose exec api python -m app.bench --concurrent 2           # two requests at once

For each chat model: its capabilities (tools, thinking, vision), then a few
typical requests — a short answer, a long one, French, a tool call — timed,
with thinking off and (for models that think) on. For each embedding model:
how many texts per second it embeds. Also the memory each model occupies.

The report prints as tables and is saved to benchmarks/<date>-compare.md —
the evidence for choosing which model fills which role (MODEL_ALIASES).
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.llm import ModelBusy, ModelError, OllamaClient

TOOLS = [{"type": "function", "function": {
    "name": "get_stock", "description": "Units in stock for a product",
    "parameters": {"type": "object", "properties": {"product": {"type": "string"}}, "required": ["product"]},
}}]

PROMPTS: list[tuple[str, list[dict], bool]] = [  # (label, messages, needs tools)
    ("short answer", [{"role": "user", "content": "In one sentence, what is a to-do list app?"}], False),
    ("long answer", [{"role": "user", "content":
                      "Write a 200-word product description for a handwoven raffia tote bag."}], False),
    ("French", [{"role": "system", "content": "Réponds en français."},
                {"role": "user", "content": "Explique en trois phrases ce qu'est une API."}], False),
    ("tool call", [{"role": "user", "content": "How many raffia bags are in stock?"}], True),
]

EMBED_TEXTS = [
    "Handwoven raffia tote bag, natural fibres, 40 x 30 cm.",
    "Savon artisanal au beurre de karité, 150 g.",
    "Online booking with SMS reminders for a clinic.",
    "Application de livraison de repas avec paiement Mobile Money.",
] * 4


async def _chat(llm: OllamaClient, model: str, messages: list[dict], tools: bool, think: bool | None) -> dict:
    body: dict = {"model": model, "messages": messages}
    if tools:
        body["tools"] = TOOLS
    if think is not None:
        body["think"] = think
    started = time.monotonic()
    try:
        async with llm.slot() as queue_ms:
            data = await llm.forward("/api/chat", body)
    except (ModelError, ModelBusy) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:140]}
    eval_ns = data.get("eval_duration") or 0
    tokens = data.get("eval_count") or 0
    return {"ok": True, "latency": time.monotonic() - started, "queue": queue_ms / 1000,
            "tps": tokens / (eval_ns / 1e9) if eval_ns else 0, "tokens": tokens,
            "tool_called": bool((data.get("message") or {}).get("tool_calls"))}


async def _bench_chat(llm: OllamaClient, model: str, runs: int, concurrent: int, lines: list[str]) -> None:
    caps = await llm.capabilities(model)
    print(f"\n{model} — {', '.join(sorted(caps)) or 'capabilities unknown'}", flush=True)
    lines += [f"### {model}", "", f"Capabilities: {', '.join(sorted(caps)) or 'unknown'}", "",
              "| Prompt | Thinking | OK | Median s | Max s | Median queue s | Tokens/s | Output tokens | Tool called |",
              "|---|---|---|---|---|---|---|---|---|"]
    await _chat(llm, model, PROMPTS[0][1], False, False if "thinking" in caps else None)  # load it
    think_modes: list[bool | None] = [False, True] if "thinking" in caps else [None]
    for label, messages, needs_tools in PROMPTS:
        if needs_tools and "tools" not in caps:
            lines.append(f"| {label} | — | — | — | — | — | — | — | no tool support |")
            continue
        for think in think_modes:
            mode = {None: "n/a", False: "off", True: "on"}[think]
            print(f"  {label} · thinking {mode}", flush=True)
            results: list[dict] = []
            for _ in range(runs):
                results += await asyncio.gather(*(_chat(llm, model, messages, needs_tools, think)
                                                  for _ in range(concurrent)))
            ok = [r for r in results if r["ok"]]
            for r in results:
                if not r["ok"]:
                    print(f"    failed: {r['error']}")
            lat = [r["latency"] for r in ok] or [0]
            tool = f"{sum(r['tool_called'] for r in ok)}/{len(ok)}" if needs_tools else "—"
            lines.append(
                f"| {label} | {mode} | {len(ok)}/{len(results)} | {statistics.median(lat):.1f} | {max(lat):.1f} | "
                f"{statistics.median([r['queue'] for r in ok] or [0]):.1f} | "
                f"{statistics.median([r['tps'] for r in ok] or [0]):.1f} | "
                f"{statistics.median([r['tokens'] for r in ok] or [0]):.0f} | {tool} |")
    lines.append("")


async def _bench_embed(llm: OllamaClient, model: str, runs: int, lines: list[str]) -> None:
    print(f"\n{model} (embeddings)", flush=True)
    timings, dims = [], 0
    for _ in range(runs + 1):  # the first run loads the model
        started = time.monotonic()
        try:
            async with llm.slot():
                data = await llm.forward("/api/embed", {"model": model, "input": EMBED_TEXTS})
        except (ModelError, ModelBusy) as exc:
            lines += [f"### {model} (embeddings)", "", f"Failed: {exc}", ""]
            return
        timings.append(time.monotonic() - started)
        dims = len((data.get("embeddings") or [[]])[0])
    median = statistics.median(timings[1:] or timings)
    lines += [f"### {model} (embeddings)", "",
              f"{len(EMBED_TEXTS)} texts in {median:.2f} s median → "
              f"{len(EMBED_TEXTS) / median:.0f} texts/s, {dims} dimensions", ""]


async def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.bench")
    parser.add_argument("--models", help="comma-separated; default: every chat and embedding model on offer")
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--concurrent", type=int, default=1, help="requests sent at once, to see queueing")
    args = parser.parse_args()

    settings = get_settings()
    if args.models:
        wanted = [m.strip() for m in args.models.split(",") if m.strip()]
        chat = [m for m in wanted if m not in settings.embedding_models()]
        embed = [m for m in wanted if m in settings.embedding_models()]
    else:
        chat, embed = settings.chat_models(), settings.embedding_models()

    llm = OllamaClient(settings.OLLAMA_URL, concurrency=settings.MODEL_CONCURRENCY,
                       queue_timeout=3600, timeout=settings.MODEL_TIMEOUT_SECONDS)
    lines = [f"# Model comparison — {datetime.now(UTC):%Y-%m-%d %H:%M} UTC", "",
             f"Runs per prompt: {args.runs}, concurrent requests: {args.concurrent}, "
             f"MODEL_CONCURRENCY={settings.MODEL_CONCURRENCY}", ""]
    try:
        for model in chat:
            await _bench_chat(llm, model, args.runs, args.concurrent, lines)
        for model in embed:
            await _bench_embed(llm, model, args.runs, lines)
        lines += ["## Memory", "", "Models in memory after the run (the rest were unloaded to make room):", ""]
        for m in await llm.loaded_models():
            on_gpu = m.get("size_vram", 0) / m["size"] * 100 if m.get("size") else 0
            lines.append(f"- {m['name']}: {m.get('size', 0) / 1024**3:.1f} GB ({on_gpu:.0f}% on GPU)")
    finally:
        await llm.close()

    report = "\n".join(lines) + "\n"
    print("\n" + report)
    out_dir = Path("benchmarks")
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"{datetime.now(UTC):%Y-%m-%d-%H%M}-compare.md"
    path.write_text(report, encoding="utf-8")
    print(f"Saved to {path}")


if __name__ == "__main__":
    asyncio.run(main())
