import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from dephx_bot.agent.approvals import ApprovalAnswer, ApprovalRequest
from dephx_bot.model import ToolSchema

# Tool names, shared by the tool modules and the phase rules in agent/phases.py.
READ_ONLY_FILE_TOOLS = ("list_files", "read_file", "search_text")
WRITE_FILE_TOOLS = ("write_file", "edit_file")
RUN_COMMAND = "run_command"
LOAD_SKILL = "load_skill"

MAX_TOOL_OUTPUT_CHARS = 12_000
# The tail gets more room than the head because test runners print their summary last.
HEAD_CHARS = 3_000
TAIL_CHARS = 9_000


class ToolError(Exception):
    """A problem the model should read and recover from, not a bug in our code."""


@dataclass(frozen=True)
class ToolResult:
    output: str
    ok: bool = True
    wrote_file: bool = False
    check_succeeded: bool | None = None
    pinned: bool = False
    # What the UI shows instead of the output, when the output is for the model only.
    display: str | None = None


@dataclass(frozen=True)
class ToolContext:
    request_approval: Callable[[ApprovalRequest], Awaitable[ApprovalAnswer]]
    cancel: asyncio.Event


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[[dict[str, Any], ToolContext], Awaitable[ToolResult]]

    def schema(self) -> ToolSchema:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def cap_output(text: str) -> str:
    if len(text) <= MAX_TOOL_OUTPUT_CHARS:
        return text
    elided = len(text) - HEAD_CHARS - TAIL_CHARS
    return f"{text[:HEAD_CHARS]}\n[... {elided} characters elided ...]\n{text[-TAIL_CHARS:]}"


def string_arg(args: dict[str, Any], name: str, default: str | None = None) -> str:
    value = args.get(name, default)
    if not isinstance(value, str):
        raise ToolError(f"Missing or invalid argument '{name}': expected a string.")
    return value


def int_arg(args: dict[str, Any], name: str, default: int) -> int:
    value = args.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ToolError(f"Invalid argument '{name}': expected a whole number.")
    return value
