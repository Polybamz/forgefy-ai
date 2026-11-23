"""Benchmark the agents on this server with representative inputs.

    docker compose exec api python -m app.bench                 # 3 runs of each sample
    docker compose exec api python -m app.bench --runs 5 --concurrent 2

Measures latency, time waiting for the model, tokens per second, schema
validity and warnings, plus the memory the model occupies. Results print as a
table and are saved to benchmarks/<date>-<model>.md, so the numbers behind a
model choice are kept with the project.
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

from app.agents import AGENTS
from app.agents.base import InvalidOutput, run_agent
from app.config import get_settings
from app.llm import ModelBusy, ModelError, OllamaClient

SAMPLES: list[tuple[str, str, dict]] = [
    ("forgefy.requirements", "meeting (en)", {
        "project_name": "Clinic booking",
        "transcript": (
            "Amina: We keep losing appointments because patients call and nobody writes it down.\n"
            "Paul: So we need online booking. Patients pick a doctor and a time slot themselves.\n"
            "Amina: Yes, and they must get an SMS reminder the day before.\n"
            "Paul: Should doctors be able to block days off?\n"
            "Amina: Definitely, doctors need to block their unavailable days.\n"
            "Paul: What about payments?\n"
            "Amina: Not now. Maybe later we take deposits, but that's nice to have.\n"
            "Paul: Reception should see all bookings for the day on one screen.\n"
            "Amina: It has to work on cheap Android phones, most patients use those."
        ),
    }),
    ("forgefy.requirements", "meeting (fr)", {
        "project_name": "Livraison de repas",
        "transcript": (
            "Chantal: Les clients commandent par WhatsApp et on perd des commandes.\n"
            "Marc: Il faut une application où le client choisit ses plats et paie avec Mobile Money.\n"
            "Chantal: Oui, et le livreur doit voir la liste des livraisons du jour avec les adresses.\n"
            "Marc: Le client doit pouvoir suivre sa commande.\n"
            "Chantal: Plus tard on pourrait ajouter un programme de fidélité, ce n'est pas urgent.\n"
            "Marc: L'application doit être en français et en anglais."
        ),
    }),
    ("vndor.product-description", "product (en)", {
        "name": "Handwoven raffia tote bag",
        "category": "Bags",
        "facts": {"material": "natural raffia", "handles": "leather", "made in": "Bamenda",
                  "size": "40 x 30 cm", "lining": "cotton"},
        "language": "en", "tone": "friendly", "max_words": 90,
    }),
    ("vndor.product-description", "product (fr)", {
        "name": "Savon artisanal au beurre de karité",
        "category": "Beauté",
        "facts": {"ingrédients": "beurre de karité, huile de coco, huile de palme rouge",
                  "poids": "150 g", "parfum": "sans parfum"},
        "price": "2 500 FCFA",
        "language": "fr", "tone": "elegant", "max_words": 80,
    }),
]


async def _one(llm: OllamaClient, agent_key: str, payload: dict, model: str) -> dict:
    agent = AGENTS[agent_key]
    inp = agent.input_model.model_validate(payload)
    started = time.monotonic()
    try:
        run = await run_agent(agent, inp, llm, model)
        return {"ok": True, "latency": time.monotonic() - started, "queue": run.queue_ms / 1000,
                "tps": run.tokens_per_second, "attempts": run.attempts, "warnings": len(run.warnings)}
    except (InvalidOutput, ModelError, ModelBusy) as exc:
        return {"ok": False, "latency": time.monotonic() - started, "error": f"{type(exc).__name__}: {exc}"[:120]}


async def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.bench")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--concurrent", type=int, default=1, help="requests sent at once, to see queueing")
    args = parser.parse_args()

    settings = get_settings()
    llm = OllamaClient(settings.OLLAMA_URL, concurrency=settings.MODEL_CONCURRENCY,
                       queue_timeout=3600, timeout=settings.MODEL_TIMEOUT_SECONDS, num_ctx=settings.NUM_CTX)
    lines = [
        f"# Benchmark {datetime.now(UTC):%Y-%m-%d %H:%M} UTC",
        "",
        f"Runs per sample: {args.runs}, concurrent requests: {args.concurrent}, "
        f"MODEL_CONCURRENCY={settings.MODEL_CONCURRENCY}, NUM_CTX={settings.NUM_CTX}",
        "",
        "| Agent | Sample | Model | OK | Median s | Max s | Median queue s | Tokens/s | Retries | Warnings |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    models_used: set[str] = set()
    try:
        print("Warming up (loads the model into memory)…", flush=True)
        await _one(llm, SAMPLES[2][0], SAMPLES[2][2], settings.model_for(SAMPLES[2][0]))
        for agent_key, label, payload in SAMPLES:
            model = settings.model_for(agent_key)
            models_used.add(model)
            print(f"{agent_key} · {label} · {model}", flush=True)
            results: list[dict] = []
            for _ in range(args.runs):
                results += await asyncio.gather(*(_one(llm, agent_key, payload, model) for _ in range(args.concurrent)))
            ok = [r for r in results if r["ok"]]
            for r in results:
                if not r["ok"]:
                    print(f"   failed: {r['error']}")
            lat = [r["latency"] for r in ok] or [0]
            tps = [r["tps"] for r in ok if r["tps"]] or [0]
            lines.append(
                f"| {agent_key} | {label} | {model} | {len(ok)}/{len(results)} | {statistics.median(lat):.1f} | "
                f"{max(lat):.1f} | {statistics.median([r['queue'] for r in ok] or [0]):.1f} | "
                f"{statistics.median(tps):.1f} | {sum(r['attempts'] - 1 for r in ok)} | "
                f"{sum(r['warnings'] for r in ok)} |"
            )
        loaded = await llm.loaded_models()
        lines += ["", "Models in memory after the run:", ""]
        for m in loaded:
            on_gpu = m.get("size_vram", 0) / m["size"] * 100 if m.get("size") else 0
            lines.append(f"- {m['name']}: {m.get('size', 0) / 1024**3:.1f} GB ({on_gpu:.0f}% on GPU)")
    finally:
        await llm.close()

    report = "\n".join(lines) + "\n"
    print("\n" + report)
    out_dir = Path("benchmarks")
    out_dir.mkdir(exist_ok=True)
    name = "-".join(sorted(m.replace(":", "_").replace("/", "_") for m in models_used))
    path = out_dir / f"{datetime.now(UTC):%Y-%m-%d-%H%M}-{name}.md"
    path.write_text(report, encoding="utf-8")
    print(f"Saved to {path}")


if __name__ == "__main__":
    asyncio.run(main())
