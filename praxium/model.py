"""The types the agent uses to talk to a model. Provider SDK types stay in providers.py."""

import math
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

# Praxiom's documented estimate for English text. Used wherever we must guess token counts.
CHARS_PER_TOKEN = 3

ToolSchema = dict[str, Any]


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    pinned: bool = False


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cached_tokens: int | None
    estimated: bool


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ModelReply:
    text: str
    tool_calls: list[ToolCall]
    usage: Usage


class ModelError(Exception):
    def __init__(
        self, code: str, message: str, status: int | None = None, retry_after: float | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retry_after = retry_after


class ModelClient(Protocol):
    def stream(
        self, messages: list[Message], tools: list[ToolSchema], max_tokens: int
    ) -> AsyncIterator[TextDelta | ModelReply]:
        """Yield text as it arrives, then exactly one ModelReply."""
        ...
