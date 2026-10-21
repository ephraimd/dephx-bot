import json
from collections.abc import AsyncIterator
from typing import Any

from praxium.model import Message, ModelError, ModelReply, TextDelta, ToolCall, ToolSchema, Usage

USAGE = Usage(1_000, 50, None, estimated=False)


class ScriptedModel:
    """A ModelClient that plays back prepared replies and records what it was sent."""

    def __init__(self, replies: list[ModelReply | ModelError]) -> None:
        self.replies = list(replies)
        self.calls: list[list[Message]] = []
        self.offered: list[set[str]] = []

    async def stream(
        self, messages: list[Message], tools: list[ToolSchema], max_tokens: int
    ) -> AsyncIterator[TextDelta | ModelReply]:
        self.calls.append(list(messages))
        self.offered.append({tool["function"]["name"] for tool in tools})
        reply = self.replies.pop(0)
        if isinstance(reply, ModelError):
            raise reply
        if reply.text:
            yield TextDelta(reply.text)
        yield reply


def text(content: str) -> ModelReply:
    return ModelReply(content, [], USAGE)


def call(name: str, arguments: dict[str, Any] | str, call_id: str | None = None) -> ModelReply:
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return ModelReply("", [ToolCall(call_id or f"call_{name}", name, raw)], USAGE)
