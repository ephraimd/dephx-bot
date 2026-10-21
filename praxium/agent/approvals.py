from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class Decision(StrEnum):
    RUN_ONCE = "run_once"
    ALWAYS = "always"
    DENY = "deny"
    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"


@dataclass(frozen=True)
class ApprovalRequest:
    kind: Literal["command", "plan"]
    text: str


@dataclass(frozen=True)
class ApprovalAnswer:
    decision: Decision
    feedback: str | None = None
