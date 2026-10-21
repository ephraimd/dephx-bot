import json

import pytest

from dephx_bot.agent.approvals import ApprovalAnswer, ApprovalRequest, Decision
from dephx_bot.agent.compaction import CompactionNotAffordable, WorkLog, split_history
from dephx_bot.agent.loop import ChatSession
from dephx_bot.model import Message, ModelReply, ToolCall, Usage
from dephx_bot.providers import PROVIDERS
from dephx_bot.tools import ToolResult
from tests.scripted_model import ScriptedModel

pytestmark = pytest.mark.anyio

NARRATIVE = "The app uses Flask. We chose SQLite for storage. Still open: the export button."


def step(call_id: str, name: str, args: dict[str, object], output: str, pinned: bool = False) -> list[Message]:
    return [
        Message("assistant", "", tool_calls=[ToolCall(call_id, name, json.dumps(args))]),
        Message("tool", output, tool_call_id=call_id, pinned=pinned),
    ]


def long_history() -> tuple[list[Message], Message]:
    request = Message("user", "Add an export button")
    messages = [
        Message("system", "sys"),
        Message("user", "Look around first"),
        *step("1", "read_file", {"path": "app.py"}, "A" * 4_000),
        *step("2", "load_skill", {"name": "verify-for-real"}, "SKILL TEXT", pinned=True),
        request,
        *step("3", "read_file", {"path": "db.py"}, "B" * 4_000),
        *step("4", "write_file", {"path": "export.py", "content": "x" * 3_000}, "Wrote 3000 characters to export.py."),
        *step("5", "run_command", {"command": "pytest"}, "Command exited with code 0.\n--- output ---\n3 passed"),
        Message("assistant", "Done for now."),
    ]
    return messages, request


def session_with(messages: list[Message], request: Message, replies: list[ModelReply], limit: int = 1_000_000) -> ChatSession:
    session = ChatSession(model=ScriptedModel(replies), spec=PROVIDERS["praxiom"], tools=[], system_prompt="sys", token_limit=limit)
    session.messages = list(messages)
    session.latest_request = request
    return session


# --- the facts log ------------------------------------------------------------------


def test_work_log_records_files_commands_and_decisions() -> None:
    log = WorkLog()
    log.record_call(ToolCall("1", "write_file", '{"path": "a.py", "content": "x"}'), ToolResult("Wrote.", wrote_file=True))
    log.record_call(ToolCall("2", "edit_file", '{"path": "a.py", "old": "x", "new": "y"}'), ToolResult("Edited.", wrote_file=True))
    log.record_call(ToolCall("3", "run_command", '{"command": "pytest -q"}'), ToolResult("Command exited with code 1.\n--- output ---\nboom", ok=False))
    log.record_decision(ApprovalRequest("plan", "1. add a.py"), ApprovalAnswer(Decision.APPROVE))
    log.record_decision(ApprovalRequest("command", "rm -rf build"), ApprovalAnswer(Decision.DENY))

    text = log.render()
    assert text.count("- a.py") == 1
    assert "`pytest -q`: Command exited with code 1." in text
    assert "You denied the command `rm -rf build`." in text
    assert "1. add a.py" in text


def test_work_log_keeps_long_commands_to_one_short_line() -> None:
    log = WorkLog()
    script = 'python3 -c "\nimport unicodedata\nfor ch in range(10):\n    print(ch)\n"'
    log.record_call(ToolCall("1", "run_command", json.dumps({"command": script})), ToolResult("Command exited with code 0.\n--- output ---\n0"))
    line = next(entry for entry in log.render().splitlines() if "exited" in entry)
    assert "import unicodedata" not in line
    assert line.startswith('- `python3 -c "') and line.endswith("Command exited with code 0.")


def test_work_log_shortens_very_long_single_line_commands() -> None:
    log = WorkLog()
    log.record_call(ToolCall("1", "run_command", json.dumps({"command": "echo " + "x" * 500})), ToolResult("Command exited with code 0."))
    assert len(log.commands[0]) < 220


# --- splitting the history ----------------------------------------------------------------


def test_split_keeps_the_last_two_steps_and_the_latest_request() -> None:
    messages, request = long_history()
    split = split_history(messages, request)

    assert split is not None
    assert split.kept[0] is request
    assert [m.tool_call_id for m in split.kept if m.role == "tool"] == ["5"]
    assert split.kept[-1].content == "Done for now."
    assert any(m.tool_call_id == "1" for m in split.old)


def test_nothing_to_compact_when_old_part_is_only_pinned() -> None:
    messages = [Message("system", "s"), Message("user", "hi"), Message("assistant", "a"), Message("assistant", "b")]
    assert split_history(messages, messages[1]) is None


# --- compacting a session -----------------------------------------------------------------


async def test_compaction_replaces_old_history_with_a_summary() -> None:
    messages, request = long_history()
    session = session_with(messages, request, [ModelReply(NARRATIVE, [], Usage(9_000, 300, None, False))])
    session.worklog.record_call(ToolCall("4", "write_file", '{"path": "export.py"}'), ToolResult("w", wrote_file=True))

    event = await session.compact(automatic=False, keep_note="Keep the CSV format")

    assert event is not None and event.after_tokens < event.before_tokens
    summary = next(m for m in session.messages if m.content.startswith("Summary of the conversation"))
    assert "export.py" in summary.content and NARRATIVE in summary.content and "Keep the CSV format" in summary.content
    assert summary.pinned
    assert any(m.content.endswith("SKILL TEXT") for m in session.messages), "loaded skills survive"
    assert session.messages[0].role == "system"
    calls = {c.id for m in session.messages for c in m.tool_calls}
    results = {m.tool_call_id for m in session.messages if m.role == "tool"}
    assert calls == results, "every tool call still has its result"


async def test_summary_request_carries_the_old_conversation_and_the_note() -> None:
    messages, request = long_history()
    model = ScriptedModel([ModelReply(NARRATIVE, [], Usage(9_000, 300, None, False))])
    session = session_with(messages, request, [])
    session.model = model

    await session.compact(automatic=False, keep_note="Mind the CSV format")

    sent = model.calls[0]
    assert sent[0].role == "system"
    assert "app.py" in sent[1].content and "Mind the CSV format" in sent[1].content
    assert model.offered[0] == set(), "the summary call offers no tools"


async def test_compaction_is_counted_and_refused_when_unaffordable() -> None:
    messages, request = long_history()
    session = session_with(messages, request, [ModelReply(NARRATIVE, [], Usage(9_000, 300, None, False))])
    await session.compact(automatic=False, keep_note=None)
    assert session.ledger.used == 9_300

    broke = session_with(messages, request, [], limit=3_000)
    with pytest.raises(CompactionNotAffordable):
        await broke.compact(automatic=False, keep_note=None)


async def test_automatic_compaction_skips_a_small_old_part() -> None:
    messages, request = long_history()
    session = session_with(messages, request, [])
    assert await session.compact(automatic=True, keep_note=None) is None
    assert session.ledger.used == 0


async def test_undo_restores_history_and_keeps_later_messages() -> None:
    messages, request = long_history()
    session = session_with(messages, request, [ModelReply(NARRATIVE, [], Usage(9_000, 300, None, False))])
    await session.compact(automatic=False, keep_note=None)
    session.messages.append(Message("assistant", "after compaction"))

    assert session.can_undo_compaction
    session.undo_compaction()

    assert session.messages[: len(messages)] == messages
    assert session.messages[-1].content == "after compaction"
    assert not session.can_undo_compaction


async def test_nothing_to_compact_returns_none_and_spends_nothing() -> None:
    messages = [Message("system", "s"), Message("user", "hi"), Message("assistant", "hello")]
    session = session_with(messages, messages[1], [])
    assert await session.compact(automatic=False, keep_note=None) is None
    assert session.ledger.used == 0
