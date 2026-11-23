"""What an agent is, and how one runs.

An agent is a versioned prompt plus strict input and output schemas. The model
is asked for JSON matching the output schema; if what comes back does not
validate, it gets one chance to correct itself. Each agent then checks the
answer against its input (`finalize`) — for invented facts, quotes that are
not in the source — and reports what it finds as warnings rather than hiding
them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pydantic import BaseModel, ValidationError

from app.llm import OllamaClient, inline_schema


class InvalidOutput(Exception):
    """The model's answer did not match the schema, even after a retry."""


class Agent:
    app: ClassVar[str]
    task: ClassVar[str]
    # Bump on any prompt or schema change; returned and logged with every
    # result so a regression can be traced to the change that caused it.
    version: ClassVar[str]
    summary: ClassVar[str]
    input_model: ClassVar[type[BaseModel]]
    output_model: ClassVar[type[BaseModel]]
    num_predict: ClassVar[int] = 2048
    temperature: ClassVar[float] = 0.2

    @property
    def key(self) -> str:
        return f"{self.app}.{self.task}"

    def check_input(self, inp: BaseModel, settings: Any) -> None:
        """Raise ValueError for input the server should not attempt."""

    def messages(self, inp: BaseModel) -> list[dict[str, str]]:
        raise NotImplementedError

    def finalize(self, inp: BaseModel, out: BaseModel) -> tuple[dict[str, Any], list[str]]:
        return out.model_dump(), []


@dataclass
class AgentRun:
    result: dict[str, Any]
    warnings: list[str]
    model: str
    queue_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    generation_ms: int = 0
    attempts: int = 1
    errors: list[str] = field(default_factory=list)

    @property
    def tokens_per_second(self) -> float | None:
        if not self.generation_ms:
            return None
        return round(self.completion_tokens / (self.generation_ms / 1000), 1)


async def run_agent(agent: Agent, inp: BaseModel, llm: OllamaClient, model: str) -> AgentRun:
    schema = inline_schema(agent.output_model.model_json_schema())
    messages = agent.messages(inp)
    run = AgentRun(result={}, warnings=[], model=model)

    for attempt in (1, 2):
        chat = await llm.chat_json(
            model, messages, schema, num_predict=agent.num_predict, temperature=agent.temperature,
        )
        run.attempts = attempt
        run.queue_ms += chat.queue_ms
        run.prompt_tokens += chat.prompt_tokens or 0
        run.completion_tokens += chat.completion_tokens or 0
        run.generation_ms += chat.generation_ms or 0
        try:
            out = agent.output_model.model_validate_json(chat.content)
            break
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'root'}: {e['msg']}" for e in exc.errors()[:8]
            )
            run.errors.append(problems)
            if attempt == 2:
                raise InvalidOutput(problems) from exc
            messages = [
                *messages,
                {"role": "assistant", "content": chat.content},
                {"role": "user", "content": f"That JSON does not match the required schema ({problems}). "
                                            "Reply again with corrected JSON only."},
            ]

    run.result, run.warnings = agent.finalize(inp, out)
    return run


# ── Helpers agents share ─────────────────────────────────────────────────────

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def normalize(text: str) -> str:
    """Casefolded words only — for checking a quote appears in its source."""
    return " ".join(_PUNCT.sub(" ", text.casefold()).split())


def quote_in(quote: str, source: str) -> bool:
    """True when the quote's words appear, in order and whole, in the source."""
    words = normalize(quote)
    return bool(words) and f" {words} " in f" {normalize(source)} "


_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def numbers_in(text: str) -> set[str]:
    return {n.replace(",", ".") for n in _NUMBER.findall(text)}
