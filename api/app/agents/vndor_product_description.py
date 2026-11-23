"""Vndor: a factual product description from merchant-supplied fields."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.base import Agent, numbers_in


class ProductInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: str | None = Field(default=None, max_length=100)
    facts: dict[str, str] = Field(
        default_factory=dict, max_length=30,
        description='What the merchant states about the product, e.g. {"material": "cotton", "sizes": "S, M, L"}',
    )
    price: str | None = Field(default=None, max_length=50, description="Only mentioned if given here")
    notes: str | None = Field(default=None, max_length=1000, description="Anything else the merchant wrote")
    language: Literal["en", "fr"] = "en"
    tone: Literal["friendly", "professional", "playful", "elegant"] = "friendly"
    max_words: int = Field(default=120, ge=30, le=400, description="Upper bound for long_description")


class ProductOutput(BaseModel):
    short_description: str = Field(description="One sentence, at most 160 characters")
    long_description: str
    highlights: list[str] = Field(description="3 to 5 short bullet points, each one a stated fact")


_SYSTEM = """You write product descriptions for small merchants' online stores (Vndor).

Rules:
- Use only the facts the merchant gave. Never invent prices, discounts, stock levels, \
delivery terms, warranties, materials, ingredients, sizes, dimensions or other \
specifications. If a detail is not given, leave it out.
- Make the given facts clear and appealing, in a {tone} tone, in {language}.
- short_description: one sentence, at most 160 characters.
- long_description: at most {max_words} words.
- highlights: 3 to 5 short points, each restating a given fact.
- Reply with JSON only."""


class VndorProductDescriptionAgent(Agent):
    app = "vndor"
    task = "product-description"
    version = "2026-11-23.1"
    summary = "Write a short and long product description from merchant-supplied facts."
    input_model = ProductInput
    output_model = ProductOutput
    num_predict = 1024
    temperature = 0.5

    def messages(self, inp: ProductInput) -> list[dict[str, str]]:
        lines = [f"Product name: {inp.name}"]
        if inp.category:
            lines.append(f"Category: {inp.category}")
        lines += [f"{k}: {v}" for k, v in inp.facts.items()]
        if inp.price:
            lines.append(f"Price: {inp.price}")
        if inp.notes:
            lines.append(f"Merchant notes: {inp.notes}")
        return [
            {"role": "system", "content": _SYSTEM.format(
                tone=inp.tone, language="French" if inp.language == "fr" else "English", max_words=inp.max_words,
            )},
            {"role": "user", "content": "\n".join(lines)},
        ]

    def finalize(self, inp: ProductInput, out: ProductOutput) -> tuple[dict[str, Any], list[str]]:
        warnings: list[str] = []
        given = " ".join([
            inp.name, inp.category or "", inp.price or "", inp.notes or "",
            *inp.facts.keys(), *inp.facts.values(),
        ])
        written = " ".join([out.short_description, out.long_description, *out.highlights])
        invented = sorted(numbers_in(written) - numbers_in(given))
        if invented:
            warnings.append(
                f"contains numbers the merchant did not give ({', '.join(invented)}) — "
                "check for invented prices or specifications before saving"
            )
        if len(out.short_description) > 200:
            warnings.append("short_description is longer than requested")
        if len(out.long_description.split()) > inp.max_words * 1.3:
            warnings.append(f"long_description is longer than {inp.max_words} words")
        return out.model_dump(), warnings
