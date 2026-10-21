import asyncio
import json
from typing import Any

from praxium.agent.approvals import ApprovalAnswer, ApprovalBroker, ApprovalCancelled, ApprovalRequest, Decision
from praxium.agent.budget import ContextFull, TokenLedger, estimate_messages, trim_history
from praxium.agent.events import (
    ApprovalRequired,
    ApprovalResolved,
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
from praxium.agent.phases import ADVANCE_PHASE, Phase, PhaseError, PhaseMachine
from praxium.agent.prompt import phase_note
from praxium.model import Message, ModelClient, ModelError, ModelReply, TextDelta, ToolCall, ToolSchema, estimate_tokens
from praxium.providers import ProviderSpec
from praxium.tools import Tool, ToolContext, ToolError, ToolResult, cap_output

MAX_STEPS = 40
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

    def context_tokens(self) -> int:
        return estimate_messages(self.messages, self._schemas())

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
        self.messages.append(Message("user", user_text))
        context = ToolContext(request_approval=lambda r: self._ask(r, approvals, emit), cancel=cancel)
        reminded = False

        for step in range(1, MAX_STEPS + 1):
            if cancel.is_set():
                return TurnStatus.STOPPED, "Stopped."

            schemas = self._schemas()
            try:
                request = trim_history(self.messages, self.spec.history_budget, schemas)
            except ContextFull:
                message = f"This chat is too long for {self.spec.label}. Start a new chat."
                await emit(ErrorRaised(code="context_full", message=message))
                return TurnStatus.FAILED, message
            request.append(Message("system", phase_note(self.phase.phase)))

            context_tokens = estimate_messages(request, schemas)
            if not self.ledger.can_afford(self.spec.hidden_prompt_tokens + context_tokens, self.spec.max_output_tokens):
                return TurnStatus.TOKEN_LIMIT, (
                    f"Token limit reached: {self.ledger.used:,} of {self.ledger.limit:,} used. "
                    "Raise the limit for this chat to continue."
                )

            try:
                reply = await self._call_model(step, request, schemas, emit)
            except ModelError as error:
                await emit(ErrorRaised(code=error.code, message=error.message))
                return TurnStatus.FAILED, error.message

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
                try:
                    result = await self._handle_call(call, context, emit)
                except ApprovalCancelled:
                    # Every tool call needs a result, or the provider rejects the next request.
                    for skipped in reply.tool_calls[index:]:
                        self.messages.append(Message("tool", NOT_RUN, tool_call_id=skipped.id))
                    return TurnStatus.STOPPED, "Stopped."
                self.messages.append(Message("tool", result.output, tool_call_id=call.id, pinned=result.pinned))

        return TurnStatus.STEP_LIMIT, f"Stopped after {MAX_STEPS} steps without finishing."

    async def _call_model(self, step: int, request: list[Message], schemas: list[ToolSchema], emit: Emit) -> ModelReply:
        async for item in self.model.stream(request, schemas, self.spec.max_output_tokens):
            if isinstance(item, TextDelta):
                await emit(TokenDelta(step=step, text=item.text))
            else:
                return item
        raise ModelError("provider_error", "The model stream ended without a reply.")

    async def _handle_call(self, call: ToolCall, context: ToolContext, emit: Emit) -> ToolResult:
        await emit(ToolStarted(call_id=call.id, name=call.name, summary=_summarise(call.arguments)))
        try:
            result = await self._run_call(call, context, emit)
        except ToolError as error:
            result = ToolResult(f"Tool error: {error}", ok=False)

        if result.wrote_file:
            self.phase.record_write()
        if result.check_succeeded is not None:
            self.phase.record_check(result.check_succeeded)

        result = ToolResult(cap_output(result.output), result.ok, pinned=result.pinned)
        await emit(
            ToolFinished(call_id=call.id, ok=result.ok, output=result.output, added_tokens=estimate_tokens(result.output))
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
        for key in ("command", "path", "pattern", "name", "to"):
            if isinstance(args.get(key), str):
                return str(args[key])[:SUMMARY_CHARS]
    return arguments[:SUMMARY_CHARS]
