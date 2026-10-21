import asyncio

import pytest

from praxium.agent.approvals import (
    ApprovalAnswer,
    ApprovalBroker,
    ApprovalCancelled,
    ApprovalRequest,
    Decision,
)
from praxium.errors import ApiError

pytestmark = pytest.mark.anyio


async def ask_in_background(
    broker: ApprovalBroker, request: ApprovalRequest
) -> tuple["asyncio.Task[ApprovalAnswer]", list[str]]:
    opened: list[str] = []

    async def on_open(approval_id: str, _request: ApprovalRequest) -> None:
        opened.append(approval_id)

    task = asyncio.ensure_future(broker.ask(request, on_open))
    await asyncio.sleep(0)
    return task, opened


async def test_resolve_wakes_the_waiter() -> None:
    broker = ApprovalBroker()
    task, opened = await ask_in_background(broker, ApprovalRequest("command", "pytest"))

    assert list(broker.pending().values()) == [ApprovalRequest("command", "pytest")]
    broker.resolve(opened[0], ApprovalAnswer(Decision.RUN_ONCE))

    assert await task == ApprovalAnswer(Decision.RUN_ONCE)
    assert broker.pending() == {}


async def test_unknown_id_rejected() -> None:
    with pytest.raises(ApiError) as error:
        ApprovalBroker().resolve("nope", ApprovalAnswer(Decision.RUN_ONCE))
    assert error.value.code == "unknown_approval"


async def test_decision_must_fit_the_kind() -> None:
    broker = ApprovalBroker()
    task, opened = await ask_in_background(broker, ApprovalRequest("plan", "1. do it"))

    with pytest.raises(ApiError) as error:
        broker.resolve(opened[0], ApprovalAnswer(Decision.RUN_ONCE))
    assert error.value.code == "invalid_decision"
    task.cancel()


async def test_request_changes_needs_feedback() -> None:
    broker = ApprovalBroker()
    task, opened = await ask_in_background(broker, ApprovalRequest("plan", "1. do it"))

    with pytest.raises(ApiError) as error:
        broker.resolve(opened[0], ApprovalAnswer(Decision.REQUEST_CHANGES, feedback="  "))
    assert error.value.code == "invalid_decision"

    broker.resolve(opened[0], ApprovalAnswer(Decision.REQUEST_CHANGES, feedback="use sqlite"))
    assert (await task).feedback == "use sqlite"


async def test_cancel_all_raises_in_the_waiter() -> None:
    broker = ApprovalBroker()
    task, _ = await ask_in_background(broker, ApprovalRequest("command", "pytest"))

    broker.cancel_all()
    with pytest.raises(ApprovalCancelled):
        await task
    assert broker.pending() == {}


async def test_answering_twice_is_rejected() -> None:
    broker = ApprovalBroker()
    task, opened = await ask_in_background(broker, ApprovalRequest("command", "pytest"))
    broker.resolve(opened[0], ApprovalAnswer(Decision.DENY))
    await task

    with pytest.raises(ApiError) as error:
        broker.resolve(opened[0], ApprovalAnswer(Decision.DENY))
    assert error.value.code == "unknown_approval"
