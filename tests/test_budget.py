import json

import pytest

from praxium.agent.budget import ELIDED_OUTPUT, ContextFull, TokenLedger, estimate_messages, trim_history
from praxium.model import Message, ToolCall, Usage


def tool_turn(call_id: str, name: str, arguments: str, output: str, pinned: bool = False) -> list[Message]:
    return [
        Message("assistant", "", tool_calls=[ToolCall(call_id, name, arguments)]),
        Message("tool", output, tool_call_id=call_id, pinned=pinned),
    ]


def test_ledger_never_overshoots() -> None:
    ledger = TokenLedger(10_000)
    ledger.record(Usage(6_000, 1_000, None, False))
    assert ledger.used == 7_000
    assert ledger.can_afford(900, 2_000)
    assert not ledger.can_afford(1_000, 2_048)


def test_ledger_limit_can_be_raised() -> None:
    ledger = TokenLedger(5_000)
    ledger.record(Usage(4_000, 500, None, False))
    ledger.set_limit(20_000)
    assert ledger.can_afford(5_000, 5_000)


def test_untrimmed_history_returned_unchanged() -> None:
    messages = [Message("system", "s"), Message("user", "hi")]
    assert trim_history(messages, 1_000, []) == messages


def test_trim_elides_old_tool_output_first() -> None:
    messages = [
        Message("system", "s"),
        Message("user", "go"),
        *tool_turn("1", "read_file", '{"path": "a.py"}', "A" * 3_000),
        *tool_turn("2", "read_file", '{"path": "b.py"}', "B" * 300),
    ]
    trimmed = trim_history(messages, estimate_messages(messages, []) - 500, [])

    assert trimmed[3].content == ELIDED_OUTPUT
    assert trimmed[5].content == "B" * 300
    assert messages[3].content == "A" * 3_000  # the original history is untouched


def test_trim_then_elides_old_write_arguments_as_valid_json() -> None:
    big_write = json.dumps({"path": "app.py", "content": "x" * 6_000})
    messages = [
        Message("system", "s"),
        Message("user", "go"),
        *tool_turn("1", "write_file", big_write, "Wrote 6000 characters to app.py."),
        *tool_turn("2", "read_file", '{"path": "b.py"}', "short"),
    ]
    trimmed = trim_history(messages, 1_000, [])

    arguments = json.loads(trimmed[2].tool_calls[0].arguments)
    assert arguments == {"path": "app.py", "elided": "wrote 6000 chars"}


def test_trim_keeps_pinned_skills() -> None:
    messages = [
        Message("system", "s"),
        Message("user", "go"),
        *tool_turn("1", "load_skill", '{"name": "verify-for-real"}', "S" * 1_500, pinned=True),
        *tool_turn("2", "read_file", '{"path": "a.py"}', "A" * 3_000),
        *tool_turn("3", "read_file", '{"path": "b.py"}', "short"),
    ]
    trimmed = trim_history(messages, estimate_messages(messages, []) - 500, [])

    assert trimmed[3].content == "S" * 1_500
    assert trimmed[5].content == ELIDED_OUTPUT


def test_trim_never_touches_the_newest_step() -> None:
    messages = [Message("system", "s"), Message("user", "go"), *tool_turn("1", "read_file", "{}", "A" * 3_000)]
    with pytest.raises(ContextFull):
        trim_history(messages, 100, [])


def test_trim_raises_context_full_when_user_messages_alone_exceed_budget() -> None:
    messages = [Message("system", "s"), Message("user", "x" * 10_000)]
    with pytest.raises(ContextFull):
        trim_history(messages, 1_000, [])


def test_tool_schemas_count_towards_the_estimate() -> None:
    messages = [Message("user", "hi")]
    schema = {"type": "function", "function": {"name": "x", "description": "d" * 300, "parameters": {}}}
    assert estimate_messages(messages, [schema]) > estimate_messages(messages, []) + 90
