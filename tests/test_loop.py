import asyncio
import sys
from pathlib import Path
from typing import TypeVar

import pytest

from praxium.agent.approvals import ApprovalAnswer, ApprovalBroker, Decision
from praxium.agent.events import (
    ApprovalRequired,
    ErrorRaised,
    Event,
    PhaseChanged,
    TokenDelta,
    ToolFinished,
    ToolStarted,
    TurnFinished,
    TurnStatus,
    UsageReported,
)
from praxium.agent.loop import ChatSession
from praxium.agent.phases import Phase
from praxium.model import ModelError, ModelReply, Usage
from praxium.providers import PROVIDERS
from praxium.settings.project import open_project
from praxium.tools.files import ProjectPaths, make_file_tools
from praxium.tools.shell import make_shell_tool
from tests.scripted_model import ScriptedModel, call, text

pytestmark = pytest.mark.anyio

PRAXIOM = PROVIDERS["praxiom"]
E = TypeVar("E")
PASSING_CHECK = f'"{sys.executable}" -c "print(1)"'


class Turn:
    """Runs one turn with a real ApprovalBroker, answering each question as it appears."""

    def __init__(self, session: ChatSession, answers: list[ApprovalAnswer] | None = None) -> None:
        self.session = session
        self.answers = list(answers or [])
        self.broker = ApprovalBroker()
        self.cancel = asyncio.Event()
        self.events: list[Event] = []
        self.stop_on_approval = False

    async def emit(self, event: Event) -> None:
        self.events.append(event)
        if isinstance(event, ApprovalRequired):
            if self.stop_on_approval:
                asyncio.get_running_loop().call_soon(self.stop)
            else:
                answer = self.answers.pop(0)
                asyncio.get_running_loop().call_soon(self.broker.resolve, event.approval_id, answer)

    def stop(self) -> None:
        self.cancel.set()
        self.broker.cancel_all()

    async def run(self, user_text: str) -> TurnStatus:
        return await self.session.run_turn(user_text, emit=self.emit, approvals=self.broker, cancel=self.cancel)

    def of(self, kind: type[E]) -> list[E]:
        return [e for e in self.events if isinstance(e, kind)]


def session_for(tmp_path: Path, replies: list[ModelReply | ModelError], token_limit: int = 1_000_000) -> tuple[ChatSession, ScriptedModel]:
    (tmp_path / "a.py").write_text("print('a')\n")
    model = ScriptedModel(replies)
    tools = [*make_file_tools(ProjectPaths(tmp_path)), make_shell_tool(open_project(str(tmp_path)))]
    session = ChatSession(model=model, spec=PRAXIOM, tools=tools, system_prompt="sys", token_limit=token_limit)
    return session, model


async def test_full_cycle_verified(tmp_path: Path) -> None:
    session, model = session_for(tmp_path, [
        call("read_file", {"path": "a.py"}),
        call("advance_phase", {"to": "ideate", "summary": "a.py prints a"}),
        call("advance_phase", {"to": "build", "summary": "1. write b.py"}),
        call("write_file", {"path": "b.py", "content": "print('b')\n"}),
        call("advance_phase", {"to": "test", "summary": "written"}),
        call("run_command", {"command": PASSING_CHECK}),
        call("advance_phase", {"to": "done", "summary": "check passed", "verified": True}),
        text("Added b.py. Verified by running it."),
    ])
    turn = Turn(session, [ApprovalAnswer(Decision.APPROVE), ApprovalAnswer(Decision.RUN_ONCE)])

    status = await turn.run("add b.py")

    assert status is TurnStatus.DONE_VERIFIED
    assert [e.phase for e in turn.of(PhaseChanged)] == ["ideate", "build", "test", "done"]
    assert (tmp_path / "b.py").exists()
    assert [e.kind for e in turn.of(ApprovalRequired)] == ["plan", "command"]


async def test_exactly_one_turn_finished_and_it_is_last(tmp_path: Path) -> None:
    session, _ = session_for(tmp_path, [text("Which file do you mean?")])
    turn = Turn(session)
    await turn.run("fix it")
    assert len(turn.of(TurnFinished)) == 1
    assert isinstance(turn.events[-1], TurnFinished)


async def test_text_streams_as_token_events(tmp_path: Path) -> None:
    session, _ = session_for(tmp_path, [text("Hello there")])
    turn = Turn(session)
    await turn.run("hi")
    assert [e.text for e in turn.of(TokenDelta)] == ["Hello there"]


async def test_plain_text_in_evaluate_waits_for_user(tmp_path: Path) -> None:
    session, _ = session_for(tmp_path, [text("Which file do you mean?")])
    assert await Turn(session).run("fix it") is TurnStatus.WAITING_FOR_USER
    assert session.phase.phase is Phase.EVALUATE


async def test_plan_request_changes_keeps_ideate_and_returns_feedback(tmp_path: Path) -> None:
    session, model = session_for(tmp_path, [
        call("advance_phase", {"to": "ideate", "summary": "looked"}),
        call("advance_phase", {"to": "build", "summary": "1. use json"}),
        text("Revised plan coming."),
    ])
    turn = Turn(session, [ApprovalAnswer(Decision.REQUEST_CHANGES, feedback="use sqlite")])

    await turn.run("store data")

    assert session.phase.phase is Phase.IDEATE
    tool_messages = [m.content for m in model.calls[-1] if m.role == "tool"]
    assert any("use sqlite" in content for content in tool_messages)


async def test_write_tool_not_offered_in_evaluate(tmp_path: Path) -> None:
    session, model = session_for(tmp_path, [text("ok")])
    await Turn(session).run("hi")
    assert "write_file" not in model.offered[0]
    assert {"read_file", "advance_phase"} <= model.offered[0]


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (call("write_file", {"path": "x.py", "content": "x"}), "not available in the evaluate phase"),
        (call("read_file", "{not json"), "not valid JSON"),
        (call("delete_everything", {}), "no tool named"),
        (call("advance_phase", {"to": "build", "summary": "skip"}), "cannot move from evaluate to build"),
    ],
)
async def test_bad_tool_calls_become_tool_errors(tmp_path: Path, reply: ModelReply, expected: str) -> None:
    session, model = session_for(tmp_path, [reply, text("sorry")])
    turn = Turn(session)

    status = await turn.run("go")

    assert status is TurnStatus.WAITING_FOR_USER
    finished = turn.of(ToolFinished)
    assert len(finished) == 1 and not finished[0].ok
    assert expected in finished[0].output
    assert not (tmp_path / "x.py").exists()


async def test_plain_text_in_build_gets_one_reminder_then_incomplete(tmp_path: Path) -> None:
    session, model = session_for(tmp_path, [text("I will now write it."), text("Still planning.")])
    session.phase.move(Phase.BUILD)

    status = await Turn(session).run("go on")

    assert status is TurnStatus.INCOMPLETE
    assert len(model.calls) == 2
    assert "build phase" in model.calls[1][-2].content


async def test_unverified_finish(tmp_path: Path) -> None:
    session, _ = session_for(tmp_path, [
        call("advance_phase", {"to": "done", "summary": "no tests exist", "verified": False}),
        text("Done, not verified: no tests exist."),
    ])
    session.phase.move(Phase.TEST)
    assert await Turn(session).run("finish") is TurnStatus.DONE_UNVERIFIED


async def test_next_message_after_done_starts_at_evaluate(tmp_path: Path) -> None:
    session, model = session_for(tmp_path, [text("What next?")])
    session.phase.move(Phase.DONE, verified=True)
    turn = Turn(session)

    await turn.run("one more thing")

    assert session.phase.phase is Phase.EVALUATE
    assert turn.of(PhaseChanged)[0].phase == "evaluate"


async def test_token_limit_stops_before_sending(tmp_path: Path) -> None:
    session, model = session_for(tmp_path, [text("never sent")], token_limit=5_000)
    status = await Turn(session).run("hi")
    assert status is TurnStatus.TOKEN_LIMIT
    assert model.calls == []


async def test_usage_event_carries_chat_totals_and_cost(tmp_path: Path) -> None:
    session, _ = session_for(tmp_path, [ModelReply("hi", [], Usage(3_000, 100, None, False))])
    turn = Turn(session)
    await turn.run("hi")

    usage = turn.of(UsageReported)[0]
    assert usage.cost == pytest.approx(0.0009381)
    assert usage.currency == "ESP"
    assert (usage.chat_used, usage.chat_limit) == (3_100, 1_000_000)
    assert usage.context_budget == PRAXIOM.history_budget


async def test_model_error_finishes_failed_with_code(tmp_path: Path) -> None:
    session, _ = session_for(tmp_path, [ModelError("model_unavailable", "Praxiom is busy.", 503, 30)])
    turn = Turn(session)

    assert await turn.run("hi") is TurnStatus.FAILED
    assert turn.of(ErrorRaised)[0].code == "model_unavailable"


async def test_stop_during_approval_finishes_stopped_and_answers_every_call(tmp_path: Path) -> None:
    session, model = session_for(
        tmp_path, [call("run_command", {"command": PASSING_CHECK}), text("next turn"), text("still building")]
    )
    session.phase.move(Phase.BUILD)
    turn = Turn(session)
    turn.stop_on_approval = True

    assert await turn.run("run it") is TurnStatus.STOPPED
    started = {e.call_id for e in turn.of(ToolStarted)}
    finished = {e.call_id for e in turn.of(ToolFinished)}
    assert started == finished  # the UI must not show a stopped tool as still running

    # The history must stay valid for the next request: every tool call has a result.
    await Turn(session).run("carry on")
    last_request = model.calls[-1]
    call_ids = {c.id for m in last_request for c in m.tool_calls}
    result_ids = {m.tool_call_id for m in last_request if m.role == "tool"}
    assert call_ids == result_ids


async def test_tool_result_reports_added_tokens(tmp_path: Path) -> None:
    session, _ = session_for(tmp_path, [call("read_file", {"path": "a.py"}), text("ok")])
    turn = Turn(session)
    await turn.run("read")
    assert turn.of(ToolFinished)[0].added_tokens > 0


async def test_tool_without_arguments_has_an_empty_summary(tmp_path: Path) -> None:
    session, _ = session_for(tmp_path, [call("list_files", {}), text("ok")])
    turn = Turn(session)
    await turn.run("look")
    assert turn.of(ToolStarted)[0].summary == ""
