import json
from collections.abc import Sequence
from dataclasses import replace

from praxium.model import Message, ToolCall, ToolSchema, Usage, estimate_tokens
from praxium.tools import WRITE_FILE_TOOLS

ELIDED_OUTPUT = "[output elided, re-run the tool if needed]"
# Role names, separators and ids cost a few tokens per message on every provider.
MESSAGE_OVERHEAD_TOKENS = 4


class ContextFull(Exception):
    pass


class TokenLedger:
    """Tokens spent in one chat, checked before every call so the limit is never overshot."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def can_afford(self, input_tokens: int, max_output: int) -> bool:
        return self.used + input_tokens + max_output <= self.limit

    def record(self, usage: Usage) -> None:
        self.used += usage.input_tokens + usage.output_tokens

    def set_limit(self, limit: int) -> None:
        self.limit = limit


def estimate_message(message: Message) -> int:
    calls = "".join(c.name + c.arguments for c in message.tool_calls)
    return estimate_tokens(message.content + calls) + MESSAGE_OVERHEAD_TOKENS


def estimate_messages(messages: Sequence[Message], tools: Sequence[ToolSchema]) -> int:
    return sum(estimate_message(m) for m in messages) + estimate_tokens(json.dumps(list(tools)))


def trim_history(messages: list[Message], budget: int, tools: Sequence[ToolSchema]) -> list[Message]:
    """Return a copy of the history that fits the budget, dropping the least useful text first.

    First old tool output (the model can re-run the tool), then the bodies of old file writes
    (the file is on disk). System and user messages, pinned skills and the newest step are kept.
    """
    trimmed = list(messages)
    total = estimate_messages(trimmed, tools)
    if total <= budget:
        return trimmed

    newest_step = max((i for i, m in enumerate(trimmed) if m.role == "assistant"), default=len(trimmed))

    for i in range(newest_step):
        message = trimmed[i]
        if message.role == "tool" and not message.pinned and message.content != ELIDED_OUTPUT:
            total -= estimate_message(message)
            trimmed[i] = replace(message, content=ELIDED_OUTPUT)
            total += estimate_message(trimmed[i])
            if total <= budget:
                return trimmed

    for i in range(newest_step):
        message = trimmed[i]
        if message.role == "assistant" and any(c.name in WRITE_FILE_TOOLS for c in message.tool_calls):
            total -= estimate_message(message)
            trimmed[i] = replace(message, tool_calls=[_elide_write(c) for c in message.tool_calls])
            total += estimate_message(trimmed[i])
            if total <= budget:
                return trimmed

    raise ContextFull()


def _elide_write(call: ToolCall) -> ToolCall:
    if call.name not in WRITE_FILE_TOOLS:
        return call
    try:
        args = json.loads(call.arguments)
        path = args.get("path", "?")
        written = args.get("content", args.get("new", ""))
        summary = {"path": path, "elided": f"wrote {len(written)} chars"}
    except (ValueError, AttributeError):
        summary = {"elided": "arguments removed"}
    # Still valid JSON: providers reject a history whose tool arguments do not parse.
    return replace(call, arguments=json.dumps(summary))
