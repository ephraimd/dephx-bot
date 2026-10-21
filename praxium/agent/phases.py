from enum import StrEnum

from praxium.tools import LOAD_SKILL, READ_ONLY_FILE_TOOLS, RUN_COMMAND, WRITE_FILE_TOOLS


class Phase(StrEnum):
    EVALUATE = "evaluate"
    IDEATE = "ideate"
    BUILD = "build"
    TEST = "test"
    DONE = "done"


ADVANCE_PHASE = "advance_phase"

_LOOKING = frozenset({*READ_ONLY_FILE_TOOLS, LOAD_SKILL, ADVANCE_PHASE})

PHASE_TOOLS: dict[Phase, frozenset[str]] = {
    Phase.EVALUATE: _LOOKING,
    Phase.IDEATE: _LOOKING,
    Phase.BUILD: _LOOKING | {*WRITE_FILE_TOOLS, RUN_COMMAND},
    Phase.TEST: frozenset({*READ_ONLY_FILE_TOOLS, RUN_COMMAND, ADVANCE_PHASE}),
    Phase.DONE: frozenset(),
}

# Shown to the model in the system prompt, so the rules it reads are the rules we enforce.
PHASE_GOALS: dict[Phase, str] = {
    Phase.EVALUATE: "Understand the request and the existing code. Leave with a summary of what exists "
    "and what the change touches.",
    Phase.IDEATE: "Weigh options, recommend one and write a short plan. The user must approve the plan "
    "before you can build.",
    Phase.BUILD: "Make the change.",
    Phase.TEST: "Prove it works by running checks. Finish verified only after a check passes after your "
    "last file change, or finish unverified and say why no check could run.",
    Phase.DONE: "Give the final report: what changed, what was verified and what was not.",
}

ALLOWED_MOVES: dict[Phase, frozenset[Phase]] = {
    Phase.EVALUATE: frozenset({Phase.IDEATE, Phase.DONE}),
    Phase.IDEATE: frozenset({Phase.BUILD, Phase.IDEATE}),
    Phase.BUILD: frozenset({Phase.TEST, Phase.IDEATE}),
    Phase.TEST: frozenset({Phase.BUILD, Phase.DONE, Phase.IDEATE}),
    Phase.DONE: frozenset(),
}


class PhaseError(Exception):
    """A refused move. The message is sent to the model, so it says what to do instead."""


class PhaseMachine:
    def __init__(self, phase: Phase = Phase.EVALUATE) -> None:
        self.phase = phase
        self.verified: bool | None = None
        # A counter, not a clock: only the order of writes and checks matters.
        self._events = 0
        self._last_write = 0
        self._last_passing_check = 0

    def allows(self, tool: str) -> bool:
        return tool in PHASE_TOOLS[self.phase]

    def record_write(self) -> None:
        self._events += 1
        self._last_write = self._events

    def record_check(self, succeeded: bool) -> None:
        self._events += 1
        if succeeded:
            self._last_passing_check = self._events

    def check_move(self, to: Phase, *, verified: bool | None) -> None:
        allowed = ALLOWED_MOVES[self.phase]
        if to not in allowed:
            names = ", ".join(sorted(p.value for p in allowed)) or "none"
            raise PhaseError(f"You cannot move from {self.phase} to {to}. Allowed next phases: {names}.")
        if to == Phase.DONE and verified and self._last_passing_check <= self._last_write:
            raise PhaseError(
                "No check has passed since your last file change. Run the tests, "
                "or finish with verified=false and explain why no check could run."
            )

    def move(self, to: Phase, *, verified: bool | None = None) -> None:
        self.phase = to
        if to == Phase.DONE:
            self.verified = bool(verified)

    def start_cycle(self) -> bool:
        if self.phase != Phase.DONE:
            return False
        self.phase = Phase.EVALUATE
        self.verified = None
        return True
