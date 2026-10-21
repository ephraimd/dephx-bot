import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from praxium.errors import ApiError


class Decision(StrEnum):
    RUN_ONCE = "run_once"
    ALWAYS = "always"
    DENY = "deny"
    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"


ApprovalKind = Literal["command", "plan"]

DECISIONS_BY_KIND: dict[ApprovalKind, frozenset[Decision]] = {
    "command": frozenset({Decision.RUN_ONCE, Decision.ALWAYS, Decision.DENY}),
    "plan": frozenset({Decision.APPROVE, Decision.REQUEST_CHANGES}),
}


@dataclass(frozen=True)
class ApprovalRequest:
    kind: ApprovalKind
    text: str


@dataclass(frozen=True)
class ApprovalAnswer:
    decision: Decision
    feedback: str | None = None


class ApprovalCancelled(Exception):
    """The turn was stopped while waiting for the user."""


class ApprovalBroker:
    """Pauses the agent until the user answers. Each question is a future the UI resolves."""

    def __init__(self) -> None:
        self._waiting: dict[str, tuple[ApprovalRequest, asyncio.Future[ApprovalAnswer]]] = {}

    async def ask(
        self, request: ApprovalRequest, on_open: Callable[[str, ApprovalRequest], Awaitable[None]]
    ) -> ApprovalAnswer:
        approval_id = uuid.uuid4().hex[:12]
        future: asyncio.Future[ApprovalAnswer] = asyncio.get_running_loop().create_future()
        self._waiting[approval_id] = (request, future)
        try:
            await on_open(approval_id, request)
            return await future
        finally:
            self._waiting.pop(approval_id, None)

    def resolve(self, approval_id: str, answer: ApprovalAnswer) -> None:
        if approval_id not in self._waiting:
            raise ApiError(404, "unknown_approval", "That question was already answered or has expired.")
        request, future = self._waiting[approval_id]
        if answer.decision not in DECISIONS_BY_KIND[request.kind]:
            raise ApiError(400, "invalid_decision", f"'{answer.decision}' is not an answer to a {request.kind} question.")
        if answer.decision == Decision.REQUEST_CHANGES and not (answer.feedback or "").strip():
            raise ApiError(400, "invalid_decision", "Say what should change.")
        self._waiting.pop(approval_id)
        future.set_result(answer)

    def cancel_all(self) -> None:
        for _, future in self._waiting.values():
            if not future.done():
                future.set_exception(ApprovalCancelled())

    def pending(self) -> dict[str, ApprovalRequest]:
        return {approval_id: request for approval_id, (request, _) in self._waiting.items()}
