import asyncio
import json
import logging
from contextlib import aclosing
from typing import Any

from dephx_bot.agent.approvals import ApprovalAnswer, ApprovalBroker, ApprovalCancelled, ApprovalRequest, Decision
from dephx_bot.agent.budget import ContextFull, TokenLedger, estimate_messages, trim_history
from dephx_bot.agent.compaction import (
    COMPACT_AT_SHARE,
    SUMMARY_MAX_TOKENS,
    CompactionNotAffordable,
    WorkLog,
    preserved_skills,
    split_history,
    summary_message,
    summary_request,
)
from dephx_bot.agent.events import (
    ApprovalRequired,
    ApprovalResolved,
    Compacted,
    Emit,
    ErrorRaised,
    PhaseChanged,
    TokenDelta,
    ToolFinished,
    ToolStarted,
    TurnFinished,
    TurnStatus,
    UsageReported,
)
from dephx_bot.agent.phases import ADVANCE_PHASE, Phase, PhaseError, PhaseMachine
from dephx_bot.agent.prompt import phase_note
from dephx_bot.model import (
    Message,
    ModelClient,
    ModelError,
    ModelReply,
    TextDelta,
    ToolCall,
    ToolSchema,
    Usage,
    estimate_tokens,
)
from dephx_bot.providers import ProviderSpec
from dephx_bot.tools import Tool, ToolContext, ToolError, ToolResult, cap_output

log = logging.getLogger(__name__)

MAX_STEPS = 40
# An automatic compaction must free at least this share of the budget, or it is not worth a model call.
MIN_COMPACTION_SHARE = 0.25
SUMMARY_CHARS = 120
NOT_RUN = "Not run: the user stopped the turn."

ADVANCE_PHASE_SCHEMA: ToolSchema = {
    "type": "function",
    "function": {
        "name": ADVANCE_PHASE,
        "description": "Move to another phase. Leaving ideate for build asks the user to approve your plan.",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "enum": [p.value for p in Phase]},
                "summary": {"type": "string", "description": "What you found, decided or did. Shown to the user."},
                "verified": {"type": "boolean", "description": "When moving to done: did a check pass?"},
            },
            "required": ["to", "summary"],
        },
    },
}


class ChatSession:
    """One chat: its history, phase and token ledger. Each user message runs one turn."""

    def __init__(
        self, *, model: ModelClient, spec: ProviderSpec, tools: list[Tool], system_prompt: str, token_limit: int
    ) -> None:
        self.model = model
        self.spec = spec
        self.tools = {tool.name: tool for tool in tools}
        self.phase = PhaseMachine()
        self.ledger = TokenLedger(token_limit)
        self.messages = [Message("system", system_prompt)]
        self.worklog = WorkLog()
        self.latest_request: Message | None = None
        # The history before the last compaction, and how long the compacted history was then.
        self._undo: tuple[list[Message], int] | None = None

    def context_tokens(self) -> int:
        return estimate_messages(self.messages, self._schemas())

    @property
    def can_undo_compaction(self) -> bool:
        return self._undo is not None

    async def compact(self, *, automatic: bool, keep_note: str | None) -> Compacted | None:
        """Replace the older history with a summary. None when there is nothing worth compacting."""
        split = split_history(self.messages, self.latest_request)
        if split is None:
            return None
        if automatic and estimate_messages([m for m in split.old if not m.pinned], []) < (
            MIN_COMPACTION_SHARE * self.spec.history_budget
        ):
            return None

        request = summary_request(split.old, keep_note, self.spec.history_budget - SUMMARY_MAX_TOKENS)
        if not self.ledger.can_afford(self.spec.hidden_prompt_tokens + estimate_messages(request, []), SUMMARY_MAX_TOKENS):
            raise CompactionNotAffordable()
        reply = await self._summarise(request)
        self.ledger.record(reply.usage)

        before = self.context_tokens()
        summary = summary_message(self.worklog, reply.text, keep_note)
        previous = list(self.messages)
        self.messages = [self.messages[0], *preserved_skills(split.old), summary, *split.kept]
        self._undo = (previous, len(self.messages))
        return Compacted(automatic=automatic, before_tokens=before, after_tokens=self.context_tokens(), summary=summary.content)

    def undo_compaction(self) -> bool:
        if self._undo is None:
            return False
        previous, compacted_length = self._undo
        # Messages added after the compaction are kept, on top of the restored history.
        self.messages = [*previous, *self.messages[compacted_length:]]
        self._undo = None
        return True

    async def run_turn(
        self, user_text: str, *, emit: Emit, approvals: ApprovalBroker, cancel: asyncio.Event
    ) -> TurnStatus:
        status, message = await self._run(user_text, emit, approvals, cancel)
        await emit(TurnFinished(status=status, message=message))
        return status

    async def _run(
        self, user_text: str, emit: Emit, approvals: ApprovalBroker, cancel: asyncio.Event
    ) -> tuple[TurnStatus, str]:
        if self.phase.start_cycle():
            await emit(PhaseChanged(phase=self.phase.phase, summary="New request."))
        self.latest_request = Message("user", user_text)
        self.messages.append(self.latest_request)
        self._undo = None
        context = ToolContext(request_approval=lambda r: self._ask(r, approvals, emit), cancel=cancel)
        reminded = False

        for step in range(1, MAX_STEPS + 1):
            if cancel.is_set():
                return TurnStatus.STOPPED, "Stopped."

            schemas = self._schemas()
            try:
                request = await self._fit(schemas, emit)
            except ContextFull:
                message = f"This chat is too long for {self.spec.label}. Start a new chat."
                await emit(ErrorRaised(code="context_full", message=message))
                return TurnStatus.FAILED, message
            except CompactionNotAffordable:
                return TurnStatus.TOKEN_LIMIT, self._limit_message()
            except ModelError as error:
                await emit(ErrorRaised(code=error.code, message=error.message))
                return TurnStatus.FAILED, error.message
            request.append(Message("system", phase_note(self.phase.phase)))

            context_tokens = estimate_messages(request, schemas)
            if not self.ledger.can_afford(self.spec.hidden_prompt_tokens + context_tokens, self.spec.max_output_tokens):
                return TurnStatus.TOKEN_LIMIT, self._limit_message()

            try:
                reply = await self._call_model(step, request, schemas, emit, cancel)
            except ModelError as error:
                await emit(ErrorRaised(code=error.code, message=error.message))
                return TurnStatus.FAILED, error.message
            if reply is None:
                # Stopped mid-stream. The provider still bills what it generated, so count an estimate.
                partial = Usage(self.spec.hidden_prompt_tokens + context_tokens, 0, None, estimated=True)
                self.ledger.record(partial)
                await emit(self._usage_event(step, ModelReply("", [], partial), context_tokens))
                return TurnStatus.STOPPED, "Stopped."

            self.ledger.record(reply.usage)
            await emit(self._usage_event(step, reply, context_tokens))
            self.messages.append(Message("assistant", reply.text, tool_calls=reply.tool_calls))

            if not reply.tool_calls:
                if self.phase.phase == Phase.DONE:
                    if self.phase.verified:
                        return TurnStatus.DONE_VERIFIED, "Finished and verified."
                    return TurnStatus.DONE_UNVERIFIED, "Finished, not verified."
                if self.phase.phase in (Phase.EVALUATE, Phase.IDEATE):
                    return TurnStatus.WAITING_FOR_USER, "Waiting for your reply."
                if reminded:
                    return TurnStatus.INCOMPLETE, "Stopped before finishing. Send a message to continue."
                reminded = True
                self.messages.append(
                    Message("user", f"You are in the {self.phase.phase} phase. Run a check or call advance_phase.")
                )
                continue

            for index, call in enumerate(reply.tool_calls):
                if cancel.is_set():
                    self._skip(reply.tool_calls[index:])
                    return TurnStatus.STOPPED, "Stopped."
                try:
                    result = await self._handle_call(call, context, emit)
                except ApprovalCancelled:
                    await emit(
                        ToolFinished(call_id=call.id, ok=False, output=NOT_RUN, added_tokens=estimate_tokens(NOT_RUN))
                    )
                    self._skip(reply.tool_calls[index:])
                    return TurnStatus.STOPPED, "Stopped."
                self.messages.append(Message("tool", result.output, tool_call_id=call.id, pinned=result.pinned))

        return TurnStatus.STEP_LIMIT, f"Stopped after {MAX_STEPS} steps without finishing."

    async def _call_model(
        self, step: int, request: list[Message], schemas: list[ToolSchema], emit: Emit, cancel: asyncio.Event
    ) -> ModelReply | None:
        """The model's reply, or None if the user stopped the turn while it was streaming."""
        # aclosing ends the provider stream at once, which also frees its concurrency slot.
        async with aclosing(self.model.stream(request, schemas, self.spec.max_output_tokens)) as stream:
            async for item in stream:
                if cancel.is_set():
                    return None
                if isinstance(item, TextDelta):
                    await emit(TokenDelta(step=step, text=item.text))
                else:
                    return item
        raise ModelError("provider_error", "The model stream ended without a reply.")

    async def _fit(self, schemas: list[ToolSchema], emit: Emit) -> list[Message]:
        """The request history: trimmed if that is enough, compacted first if it is not."""
        budget = self.spec.history_budget
        try:
            request = trim_history(self.messages, budget, schemas)
            if estimate_messages(request, schemas) <= COMPACT_AT_SHARE * budget:
                return request
        except ContextFull:
            pass
        compacted = await self.compact(automatic=True, keep_note=None)
        if compacted is not None:
            await emit(compacted)
        return trim_history(self.messages, budget, schemas)

    async def _summarise(self, request: list[Message]) -> ModelReply:
        async with aclosing(self.model.stream(request, [], SUMMARY_MAX_TOKENS)) as stream:
            async for item in stream:
                if isinstance(item, ModelReply):
                    return item
        raise ModelError("provider_error", "The model stream ended without a summary.")

    def _limit_message(self) -> str:
        return (
            f"Token limit reached: {self.ledger.used:,} of {self.ledger.limit:,} used. "
            "Raise the limit for this chat to continue."
        )

    def _skip(self, calls: list[ToolCall]) -> None:
        # Every tool call needs a result, or the provider rejects the next request.
        for call in calls:
            self.messages.append(Message("tool", NOT_RUN, tool_call_id=call.id))

    async def _handle_call(self, call: ToolCall, context: ToolContext, emit: Emit) -> ToolResult:
        await emit(ToolStarted(call_id=call.id, name=call.name, summary=_summarise(call.arguments)))
        try:
            result = await self._run_call(call, context, emit)
        except ToolError as error:
            result = ToolResult(f"Tool error: {error}", ok=False)
        except ApprovalCancelled:
            raise
        except Exception as error:
            # A tool bug must not end the turn: the call still needs a result to keep the history valid.
            log.exception("tool %s failed", call.name)
            result = ToolResult(f"Tool error: {type(error).__name__}: {error}", ok=False)

        self.worklog.record_call(call, result)
        if result.wrote_file:
            self.phase.record_write()
        if result.check_succeeded is not None:
            self.phase.record_check(result.check_succeeded)

        shown = result.display
        # Pinned results (loaded skills) are known, trusted text: cutting their middle would lose guidance.
        output = result.output if result.pinned else cap_output(result.output)
        result = ToolResult(output, result.ok, pinned=result.pinned)
        await emit(
            ToolFinished(
                call_id=call.id,
                ok=result.ok,
                output=result.output if shown is None else shown,
                added_tokens=estimate_tokens(result.output),
            )
        )
        return result

    async def _run_call(self, call: ToolCall, context: ToolContext, emit: Emit) -> ToolResult:
        if call.name != ADVANCE_PHASE and call.name not in self.tools:
            raise ToolError(f"There is no tool named '{call.name}'.")
        if not self.phase.allows(call.name):
            raise ToolError(f"{call.name} is not available in the {self.phase.phase} phase. {phase_note(self.phase.phase)}")
        try:
            args = json.loads(call.arguments or "{}")
        except json.JSONDecodeError as error:
            raise ToolError(f"The arguments are not valid JSON: {error}.") from error
        if not isinstance(args, dict):
            raise ToolError("The arguments must be a JSON object.")

        if call.name == ADVANCE_PHASE:
            return await self._advance(args, context, emit)
        return await self.tools[call.name].run(args, context)

    async def _advance(self, args: dict[str, Any], context: ToolContext, emit: Emit) -> ToolResult:
        target = args.get("to")
        if target not in {p.value for p in Phase}:
            raise ToolError(f"Unknown phase {target!r}.")
        to = Phase(target)
        summary = str(args.get("summary", ""))
        verified = args.get("verified")
        if verified is not None and not isinstance(verified, bool):
            raise ToolError("verified must be true or false.")

        try:
            self.phase.check_move(to, verified=verified)
        except PhaseError as error:
            raise ToolError(str(error)) from error

        if self.phase.phase == Phase.IDEATE and to == Phase.BUILD:
            answer = await context.request_approval(ApprovalRequest("plan", summary))
            if answer.decision == Decision.REQUEST_CHANGES:
                return ToolResult(f"The user requested changes to the plan: {answer.feedback}", ok=False)

        self.phase.move(to, verified=verified)
        await emit(PhaseChanged(phase=to, summary=summary))
        if to == Phase.DONE:
            return ToolResult("Now in the done phase. Write the final report.")
        return ToolResult(f"Now in the {to} phase.")

    async def _ask(self, request: ApprovalRequest, approvals: ApprovalBroker, emit: Emit) -> ApprovalAnswer:
        opened: list[str] = []

        async def announce(approval_id: str, question: ApprovalRequest) -> None:
            opened.append(approval_id)
            await emit(ApprovalRequired(approval_id=approval_id, kind=question.kind, text=question.text))

        answer = await approvals.ask(request, announce)
        self.worklog.record_decision(request, answer)
        await emit(ApprovalResolved(approval_id=opened[0], decision=answer.decision))
        return answer

    def _schemas(self) -> list[ToolSchema]:
        schemas = [tool.schema() for name, tool in self.tools.items() if self.phase.allows(name)]
        if self.phase.allows(ADVANCE_PHASE):
            schemas.append(ADVANCE_PHASE_SCHEMA)
        return schemas

    def _usage_event(self, step: int, reply: ModelReply, context_tokens: int) -> UsageReported:
        usage = reply.usage
        return UsageReported(
            step=step,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_tokens=usage.cached_tokens,
            estimated=usage.estimated,
            cost=self.spec.cost(usage),
            currency=self.spec.currency,
            chat_used=self.ledger.used,
            chat_limit=self.ledger.limit,
            context_tokens=context_tokens,
            context_budget=self.spec.history_budget,
        )


def _summarise(arguments: str) -> str:
    """A short label for the UI's tool block: the command, path or phase, not the whole payload."""
    try:
        args = json.loads(arguments)
    except json.JSONDecodeError:
        return arguments[:SUMMARY_CHARS]
    if isinstance(args, dict):
        if not args:
            return ""
        for key in ("command", "path", "pattern", "name", "to"):
            if isinstance(args.get(key), str):
                return str(args[key])[:SUMMARY_CHARS]
    return arguments[:SUMMARY_CHARS]
