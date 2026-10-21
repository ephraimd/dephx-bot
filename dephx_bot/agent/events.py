"""Everything the UI learns about a turn. Mirrored by hand in ui/src/lib/events.ts: change both."""

from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from dephx_bot.agent.approvals import ApprovalKind, Decision
from dephx_bot.agent.phases import Phase


class TurnStatus(StrEnum):
    DONE_VERIFIED = "done_verified"
    DONE_UNVERIFIED = "done_unverified"
    WAITING_FOR_USER = "waiting_for_user"
    INCOMPLETE = "incomplete"
    STOPPED = "stopped"
    STEP_LIMIT = "step_limit"
    TOKEN_LIMIT = "token_limit"
    FAILED = "failed"


class PhaseChanged(BaseModel):
    type: Literal["phase_changed"] = "phase_changed"
    phase: Phase
    summary: str


class TokenDelta(BaseModel):
    type: Literal["token"] = "token"
    step: int
    text: str


class UsageReported(BaseModel):
    type: Literal["usage"] = "usage"
    step: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int | None
    estimated: bool
    cost: float | None
    currency: str | None
    chat_used: int
    chat_limit: int
    context_tokens: int
    context_budget: int


class ToolStarted(BaseModel):
    type: Literal["tool_started"] = "tool_started"
    call_id: str
    name: str
    summary: str


class ToolFinished(BaseModel):
    type: Literal["tool_finished"] = "tool_finished"
    call_id: str
    ok: bool
    output: str
    added_tokens: int


class ApprovalRequired(BaseModel):
    type: Literal["approval_required"] = "approval_required"
    approval_id: str
    kind: ApprovalKind
    text: str


class ApprovalResolved(BaseModel):
    type: Literal["approval_resolved"] = "approval_resolved"
    approval_id: str
    decision: Decision


class Compacted(BaseModel):
    type: Literal["compacted"] = "compacted"
    automatic: bool
    before_tokens: int
    after_tokens: int
    summary: str


class TurnFinished(BaseModel):
    type: Literal["turn_finished"] = "turn_finished"
    status: TurnStatus
    message: str


class ErrorRaised(BaseModel):
    type: Literal["error"] = "error"
    code: str
    message: str


Event = Annotated[
    PhaseChanged
    | TokenDelta
    | UsageReported
    | ToolStarted
    | ToolFinished
    | ApprovalRequired
    | ApprovalResolved
    | Compacted
    | TurnFinished
    | ErrorRaised,
    Field(discriminator="type"),
]

Emit = Callable[[Event], Awaitable[None]]
