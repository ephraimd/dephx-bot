"""Compaction: replace the older part of a chat with a summary when the history gets too big.

Trimming (budget.py) hides old tool output from a single request and costs nothing. Compaction
goes further: it rewrites the older messages for good. The facts in the summary come from the
app's own records (WorkLog), so a model cannot drop or invent which files changed or which checks
passed; the model only writes what it learned and what is still open.
"""

import json
from dataclasses import dataclass, field

from praxium.agent.approvals import ApprovalAnswer, ApprovalRequest, Decision
from praxium.model import CHARS_PER_TOKEN, Message, ToolCall
from praxium.tools import RUN_COMMAND, ToolResult

# Compact before the history fills the window: the summary call itself needs room to fit.
COMPACT_AT_SHARE = 0.85
KEEP_RECENT_STEPS = 2
SUMMARY_MAX_TOKENS = 1_500
MESSAGE_CHARS_IN_SUMMARY_REQUEST = 1_500
# A fact log needs to say which command ran, not reproduce a multi-line script.
COMMAND_CHARS_IN_LOG = 150
SUMMARY_HEADING = "Summary of the conversation so far"
SKILL_HEADING = "Guidance you loaded earlier with load_skill:"

SUMMARY_PROMPT = """\
You are summarising part of a coding session so the engineer agent can continue it with less \
context. Write plain sentences, under 800 words, covering:
- what was learned about the code and the project,
- the decisions taken and the reasons for them,
- what is still open or should happen next.
Do not list files changed, commands run or approvals: the app records those separately. Do not \
invent anything that is not in the conversation.
"""


class CompactionNotAffordable(Exception):
    """The chat's token limit has no room for the summary call."""


@dataclass
class WorkLog:
    """What actually happened in the chat, recorded by the app as it happened."""

    files: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    plan: str | None = None

    def record_call(self, call: ToolCall, result: ToolResult) -> None:
        args = _arguments(call)
        path, command = args.get("path"), args.get("command")
        if result.wrote_file and isinstance(path, str) and path not in self.files:
            self.files.append(path)
        if call.name == RUN_COMMAND and isinstance(command, str):
            status = result.output.splitlines()[0] if result.output else "no output"
            self.commands.append(f"`{_one_line(command)}`: {status}")

    def record_decision(self, request: ApprovalRequest, answer: ApprovalAnswer) -> None:
        if request.kind == "plan":
            if answer.decision == Decision.APPROVE:
                self.plan = request.text
                self.decisions.append("You approved the plan below.")
            else:
                self.decisions.append(f"You asked for changes to a plan: {answer.feedback}")
        elif answer.decision == Decision.DENY:
            self.decisions.append(f"You denied the command `{request.text}`.")
        elif answer.decision == Decision.ALWAYS:
            self.decisions.append(f"You always allowed the command `{request.text}` in this project.")

    def render(self) -> str:
        sections = [
            ("Files changed", [f"- {path}" for path in self.files]),
            ("Commands run", [f"- {command}" for command in self.commands]),
            ("Decisions by the user", [f"- {decision}" for decision in self.decisions]),
            ("Approved plan", [self.plan] if self.plan else []),
        ]
        return "\n\n".join(f"### {title}\n" + ("\n".join(lines) or "None yet.") for title, lines in sections)


@dataclass(frozen=True)
class Split:
    old: list[Message]
    kept: list[Message]


def split_history(messages: list[Message], latest_request: Message | None) -> Split | None:
    """Old messages to summarise, and the ones kept as they are. None when nothing can be compacted.

    Kept: the latest user request and the last KEEP_RECENT_STEPS model steps with their tool
    results. Cutting at an assistant message keeps every tool call next to its result.
    """
    body = messages[1:]
    steps = [i for i, message in enumerate(body) if message.role == "assistant"]
    if len(steps) <= KEEP_RECENT_STEPS:
        return None
    boundary = steps[-KEEP_RECENT_STEPS]
    old = [m for m in body[:boundary] if m is not latest_request]
    # Only skills and an earlier summary left: compacting again would just rewrite the summary.
    if all(m.pinned for m in old):
        return None
    request = [m for m in body[:boundary] if m is latest_request]
    return Split(old=old, kept=[*request, *body[boundary:]])


def summary_request(old: list[Message], keep_note: str | None, budget_tokens: int) -> list[Message]:
    """The messages for the summary call: the old conversation as plain text, newest kept if too long."""
    # Earlier summaries are included, so their notes carry into the new one; skills are kept separately.
    lines = [_as_text(message) for message in old if not is_skill(message)]
    budget_chars = budget_tokens * CHARS_PER_TOKEN
    while lines and sum(len(line) for line in lines) > budget_chars:
        lines.pop(0)
    note = f"\n\nThe user asked you to keep this in mind: {keep_note}" if keep_note else ""
    return [
        Message("system", SUMMARY_PROMPT),
        Message("user", "Summarise this part of the session:\n\n" + "\n\n".join(lines) + note),
    ]


def summary_message(log: WorkLog, narrative: str, keep_note: str | None) -> Message:
    note = f"\n\n## The user asked to keep in mind\n{keep_note}" if keep_note else ""
    content = (
        f"{SUMMARY_HEADING} (older messages were replaced by this summary to save space).\n\n"
        f"## Recorded by the app\n{log.render()}\n\n## Notes written when compacting\n{narrative.strip()}{note}"
    )
    return Message("user", content, pinned=True)


def preserved_skills(old: list[Message]) -> list[Message]:
    """Loaded skills are pinned tool results; their calls go away, so they come back as plain messages."""
    kept = []
    for message in old:
        if message.role == "tool" and message.pinned:
            kept.append(Message("user", f"{SKILL_HEADING}\n\n{message.content}", pinned=True))
        elif is_skill(message):
            kept.append(message)
    return kept


def is_skill(message: Message) -> bool:
    return message.pinned and (
        message.role == "tool" or (message.role == "user" and message.content.startswith(SKILL_HEADING))
    )


def _one_line(command: str) -> str:
    lines = command.strip().splitlines() or [""]
    first = lines[0]
    if len(lines) > 1 or len(first) > COMMAND_CHARS_IN_LOG:
        return first[:COMMAND_CHARS_IN_LOG].rstrip() + " ..."
    return first


def _as_text(message: Message) -> str:
    content = message.content[:MESSAGE_CHARS_IN_SUMMARY_REQUEST]
    if message.role == "tool":
        return f"Result: {content}"
    if message.role == "assistant":
        calls = "; ".join(f"{c.name} {c.arguments[:200]}" for c in message.tool_calls)
        return f"Agent: {content}" + (f" [called {calls}]" if calls else "")
    return f"User: {content}"


def _arguments(call: ToolCall) -> dict[str, object]:
    try:
        parsed = json.loads(call.arguments or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
