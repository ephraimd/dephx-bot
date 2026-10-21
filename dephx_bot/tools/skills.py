from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dephx_bot.model import estimate_tokens
from dephx_bot.tools import LOAD_SKILL, Tool, ToolContext, ToolError, ToolResult, string_arg


@dataclass(frozen=True)
class SkillInfo:
    name: str
    description: str


class SkillLibrary:
    """The vendored senior-dephx snapshot: GATE.md is always in the prompt, skills load on demand."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def gate(self) -> str:
        return (self.root / "core" / "GATE.md").read_text(encoding="utf-8")

    def index(self) -> list[SkillInfo]:
        skills = []
        for path in sorted((self.root / "skills").glob("*/SKILL.md")):
            fields = _frontmatter(path.read_text(encoding="utf-8"))
            skills.append(SkillInfo(fields.get("name", path.parent.name), fields.get("description", "")))
        return skills

    def load(self, name: str) -> str:
        names = [skill.name for skill in self.index()]
        # Checking against the index also stops names like "../core" reaching the filesystem.
        if name not in names:
            raise ToolError(f"Unknown skill '{name}'. Available skills: {', '.join(names)}.")
        return (self.root / "skills" / name / "SKILL.md").read_text(encoding="utf-8")


def make_skill_tool(library: SkillLibrary) -> Tool:
    async def load_skill(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        name = string_arg(args, "name")
        text = library.load(name)
        # The guidance is the harness's own method, so the UI names it but does not print it.
        shown = f"Loaded the {name} guidance ({estimate_tokens(text):,} tokens)."
        return ToolResult(text, pinned=True, display=shown)

    return Tool(
        LOAD_SKILL,
        "Load one senior-dephx skill: the full guidance for a kind of engineering work.",
        {
            "type": "object",
            "properties": {"name": {"type": "string", "enum": [s.name for s in library.index()]}},
            "required": ["name"],
        },
        load_skill,
    )


def _frontmatter(text: str) -> dict[str, str]:
    """Read the name/description lines between the opening '---' markers. Values are one line each."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fields = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields
