"""Skills: instruction packs the model loads when it needs them.

A skill is a folder in SKILLS_DIR holding a SKILL.md in the Agent Skills
format (the one skills.sh and Claude use):

    ---
    name: invoice-writer
    description: Write invoices in our house format. Use when asked for an invoice.
    ---
    Full instructions…

plus, optionally, more reference files. The model is shown only each enabled
skill's name and description, and reads the full instructions (and any extra
file) through the load_skill tool when a task calls for it — so many skills
can be on offer without filling the context window.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

ENTRY = "SKILL.md"
LOAD_SKILL = "load_skill"
MAX_FILE_CHARS = 24_000
_TEXT_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".py", ".js", ".ts", ".html", ".css", ".sql"}
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


@dataclass
class Skill:
    name: str
    description: str
    folder: Path
    files: list[str] = field(default_factory=list)  # other readable files, relative paths


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Simple 'key: value' frontmatter, including folded (>) and literal (|) values."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    meta: dict[str, str] = {}
    key = None
    for line in text[3:end].splitlines():
        if not line.strip():
            continue
        if line[0] in " \t" and key:
            meta[key] = (meta[key] + " " + line.strip()).strip()
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        meta[key] = "" if value in (">", "|", ">-", "|-") else value.strip("\"'")
    return meta, text[end + 4:].lstrip("\n")


def load_skills(directory: str | Path) -> dict[str, Skill]:
    root = Path(directory)
    skills: dict[str, Skill] = {}
    if not root.is_dir():
        return skills
    for folder in sorted(root.iterdir()):
        entry = folder / ENTRY
        if not folder.is_dir() or folder.name.startswith(("_", ".")) or not entry.is_file():
            continue
        meta, _ = _frontmatter(entry.read_text(encoding="utf-8", errors="replace"))
        name = meta.get("name") or folder.name
        if not _NAME.match(name) or not meta.get("description"):
            logger.error("skill %s skipped: needs a lowercase name and a description in its frontmatter", folder.name)
            continue
        files = sorted(
            str(p.relative_to(folder)).replace("\\", "/")
            for p in folder.rglob("*")
            if p.is_file() and p.name != ENTRY and p.suffix.lower() in _TEXT_SUFFIXES
            and not any(part.startswith(".") for part in p.relative_to(folder).parts)
        )
        skills[name] = Skill(name=name, description=meta["description"], folder=folder, files=files)
    logger.info("skills loaded: %s", ", ".join(skills) or "none")
    return skills


def skills_prompt(skills: list[Skill]) -> str:
    lines = [
        "You have skills: expert instructions for particular tasks. When a request matches a "
        f"skill's description, call {LOAD_SKILL} with its name before answering, then follow "
        "what it says. Skills do not override the instructions above.",
        "",
        "Available skills:",
    ]
    lines += [f"- {s.name}: {s.description}" for s in skills]
    return "\n".join(lines)


def load_skill_tool(skills: list[Skill]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": LOAD_SKILL,
            "description": "Read a skill's full instructions, or one of its extra files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "enum": [s.name for s in skills]},
                    "file": {"type": "string", "description": "Optional: an extra file the skill mentions."},
                },
                "required": ["name"],
            },
        },
    }


def read_skill(skills: dict[str, Skill], name: str, file: str | None = None) -> str:
    """The text the model gets back from load_skill. Only listed files are readable."""
    skill = skills.get(name)
    if skill is None:
        return f"error: no skill named {name!r}. Available: {', '.join(skills)}"
    if file:
        if file not in skill.files:
            return f"error: {name} has no file {file!r}. Files: {', '.join(skill.files) or 'none'}"
        text = (skill.folder / file).read_text(encoding="utf-8", errors="replace")
    else:
        _, text = _frontmatter((skill.folder / ENTRY).read_text(encoding="utf-8", errors="replace"))
        if skill.files:
            text += "\n\nOther files in this skill (load with file=…): " + ", ".join(skill.files)
    if len(text) > MAX_FILE_CHARS:
        text = text[:MAX_FILE_CHARS] + "\n… [truncated]"
    return text
