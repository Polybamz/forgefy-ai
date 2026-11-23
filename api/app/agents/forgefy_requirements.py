"""Forgefy: structured requirements from a meeting transcript (segment)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.base import Agent, quote_in


class RequirementsInput(BaseModel):
    transcript: str = Field(min_length=20, description="Transcript text, ideally with 'Speaker: ' prefixes.")
    project_name: str | None = Field(default=None, max_length=200)
    language: Literal["auto", "en", "fr"] = Field(
        default="auto", description="Language to write the output in; 'auto' follows the transcript.",
    )


class Requirement(BaseModel):
    id: str = Field(description="FR-1, FR-2, … in order of appearance")
    description: str
    priority: Literal["must", "should", "could"]
    source_quote: str | None = Field(
        description="Short verbatim words from the transcript that state this requirement, or null",
    )


class AcceptanceCriterion(BaseModel):
    requirement_id: str
    criterion: str


class RequirementsOutput(BaseModel):
    summary: str = Field(description="Two or three sentences on what the meeting wants built")
    user_roles: list[str]
    problems: list[str]
    goals: list[str]
    functional_requirements: list[Requirement]
    constraints: list[str]
    acceptance_criteria: list[AcceptanceCriterion]
    open_questions: list[str] = Field(description="Ambiguities, contradictions and missing information")
    language: Literal["en", "fr"]


_SYSTEM = """You extract software requirements from meeting transcripts for Forgefy, \
which turns meetings into product blueprints.

Rules:
- Use only what the transcript says. Never invent features, users, numbers, names or \
deadlines. When something important is unclear, contradictory or missing, add it to \
open_questions instead of guessing.
- When a later statement changes an earlier one, keep the later decision and mention the \
change in open_questions if it is not clearly settled.
- functional_requirements: one per distinct capability, ids FR-1, FR-2, … Priority: \
"must" when it is stated as essential, "should" when wanted, "could" when optional or \
"nice to have".
- source_quote: copy a few words exactly as they appear in the transcript (at most 25 \
words) that state the requirement; null when no single passage does.
- acceptance_criteria: testable conditions, each tied to a requirement id.
- Write every field in {language}. The transcript may mix English and French.
- Reply with JSON only."""


class ForgefyRequirementsAgent(Agent):
    app = "forgefy"
    task = "requirements"
    version = "2026-11-23.1"
    summary = "Extract structured requirements from a meeting transcript."
    input_model = RequirementsInput
    output_model = RequirementsOutput
    num_predict = 3072

    def check_input(self, inp: RequirementsInput, settings: Any) -> None:
        if len(inp.transcript) > settings.MAX_TRANSCRIPT_CHARS:
            raise ValueError(
                f"transcript is {len(inp.transcript)} characters; the limit is "
                f"{settings.MAX_TRANSCRIPT_CHARS}. Send it in segments."
            )

    def messages(self, inp: RequirementsInput) -> list[dict[str, str]]:
        language = {
            "en": "English", "fr": "French", "auto": "the main language of the transcript (English or French)",
        }[inp.language]
        header = f"Project: {inp.project_name}\n\n" if inp.project_name else ""
        return [
            {"role": "system", "content": _SYSTEM.format(language=language)},
            {"role": "user", "content": f"{header}Transcript:\n\"\"\"\n{inp.transcript}\n\"\"\""},
        ]

    def finalize(self, inp: RequirementsInput, out: RequirementsOutput) -> tuple[dict[str, Any], list[str]]:
        warnings: list[str] = []
        result = out.model_dump()

        for req in result["functional_requirements"]:
            quote = req["source_quote"]
            req["quote_verified"] = bool(quote) and quote_in(quote, inp.transcript)
            if quote and not req["quote_verified"]:
                warnings.append(f"{req['id']}: source_quote is not in the transcript — check this requirement")

        ids = {r["id"] for r in result["functional_requirements"]}
        for criterion in result["acceptance_criteria"]:
            if criterion["requirement_id"] not in ids:
                warnings.append(f"acceptance criterion refers to unknown requirement {criterion['requirement_id']}")
        if not ids:
            warnings.append("no functional requirements found in this transcript")
        return result, warnings
