import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Callable

from pydantic import BaseModel

from praxium.agent.approvals import ApprovalAnswer, ApprovalBroker
from praxium.agent.compaction import CompactionNotAffordable
from praxium.agent.events import ApprovalRequired, Compacted, ErrorRaised, Event, TurnFinished, TurnStatus
from praxium.agent.loop import ChatSession
from praxium.agent.phases import Phase
from praxium.agent.prompt import build_system_prompt
from praxium.errors import ApiError
from praxium.model import ModelClient, ModelError
from praxium.providers import PROVIDERS, ProviderSpec
from praxium.settings.app_settings import AppSettingsStore
from praxium.settings.project import Project, ProjectView, open_project
from praxium.tools.files import ProjectPaths, make_file_tools
from praxium.tools.shell import make_shell_tool
from praxium.tools.skills import SkillLibrary, make_skill_tool

log = logging.getLogger(__name__)

ClientFor = Callable[[ProviderSpec, str], ModelClient]


class ChatState(BaseModel):
    project: ProjectView | None
    provider: str | None
    phase: Phase
    chat_used: int
    chat_limit: int
    context_tokens: int
    context_budget: int
    running_turn: str | None
    pending_approvals: list[ApprovalRequired]
    can_undo_compaction: bool


class TurnRecord:
    """Every event of one turn, kept so a reloaded window can replay the turn from the start."""

    def __init__(self, turn_id: str) -> None:
        self.id = turn_id
        self.events: list[Event] = []
        self._changed = asyncio.Condition()

    @property
    def finished(self) -> bool:
        return bool(self.events) and isinstance(self.events[-1], TurnFinished)

    async def add(self, event: Event) -> None:
        self.events.append(event)
        async with self._changed:
            self._changed.notify_all()

    async def wait_beyond(self, count: int) -> None:
        async with self._changed:
            await self._changed.wait_for(lambda: len(self.events) > count)


class ChatService:
    """The single open chat: its project, session, running turn and pending questions."""

    def __init__(
        self,
        *,
        settings: AppSettingsStore,
        skills: SkillLibrary,
        client_for: ClientFor,
        os_name: str,
        shell_name: str,
        command_env: dict[str, str],
    ) -> None:
        self.settings = settings
        self.skills = skills
        self.client_for = client_for
        self.os_name = os_name
        self.shell_name = shell_name
        self.command_env = command_env
        self.project: Project | None = None
        self._session: ChatSession | None = None
        self._limit_for_next_chat: int | None = None
        self._broker = ApprovalBroker()
        self._turns: dict[str, TurnRecord] = {}
        self._running: TurnRecord | None = None
        self._compacting = False
        self._cancel = asyncio.Event()
        # Held so the task is not garbage-collected while it runs.
        self._task: asyncio.Task[None] | None = None

    def open_project(self, path: str) -> ProjectView:
        self._refuse_while_running()
        self.project = open_project(path)
        self._reset_chat()
        return self.project.view()

    def trust_project(self) -> ProjectView:
        project = self._require_project()
        project.trust()
        return project.view()

    def new_chat(self) -> None:
        self._refuse_while_running()
        self._reset_chat()

    async def start_turn(self, text: str) -> str:
        self._refuse_while_running()
        project = self._require_project()
        if not text.strip():
            raise ApiError(400, "empty_message", "Write a message first.")
        session = self._session or self._new_session(project)
        self._session = session

        record = TurnRecord(uuid.uuid4().hex[:12])
        self._turns[record.id] = record
        self._running = record
        self._cancel = asyncio.Event()
        self._task = asyncio.create_task(self._run(record, session, text))
        return record.id

    async def events(self, turn_id: str) -> AsyncIterator[Event]:
        record = self.turn(turn_id)
        sent = 0
        while True:
            while sent < len(record.events):
                event = record.events[sent]
                sent += 1
                yield event
                if isinstance(event, TurnFinished):
                    return
            await record.wait_beyond(sent)

    def turn(self, turn_id: str) -> TurnRecord:
        if turn_id not in self._turns:
            raise ApiError(404, "unknown_turn", "That turn belongs to an earlier chat.")
        return self._turns[turn_id]

    def answer(self, approval_id: str, answer: ApprovalAnswer) -> None:
        self._broker.resolve(approval_id, answer)

    def stop(self) -> None:
        self._cancel.set()
        self._broker.cancel_all()

    async def compact(self, keep_note: str | None) -> Compacted:
        self._refuse_while_running()
        if self._session is None:
            raise ApiError(409, "nothing_to_compact", "There is nothing to compact yet.")
        self._compacting = True
        try:
            event = await self._session.compact(automatic=False, keep_note=(keep_note or "").strip() or None)
        except CompactionNotAffordable as error:
            raise ApiError(
                400, "token_limit", "Compacting needs one model call, and this chat's token limit has no room for it."
            ) from error
        except ModelError as error:
            raise ApiError(502, error.code, error.message) from error
        finally:
            self._compacting = False
        if event is None:
            raise ApiError(409, "nothing_to_compact", "There is not enough history to compact yet.")
        return event

    def undo_compaction(self) -> ChatState:
        self._refuse_while_running()
        if self._session is None or not self._session.undo_compaction():
            raise ApiError(409, "nothing_to_undo", "There is no compaction to undo.")
        return self.state()

    def set_token_limit(self, limit: int) -> None:
        spec = self._session.spec if self._session else PROVIDERS[self.settings.load().provider]
        if limit < spec.min_token_limit():
            raise ApiError(
                400, "token_limit_too_low", f"The token limit must be at least {spec.min_token_limit():,} for {spec.label}."
            )
        if self._session:
            self._session.ledger.set_limit(limit)
        else:
            self._limit_for_next_chat = limit

    def state(self) -> ChatState:
        session = self._session
        settings = self.settings.load()
        spec = session.spec if session else PROVIDERS[settings.provider]
        return ChatState(
            project=self.project.view() if self.project else None,
            provider=session.spec.id if session else None,
            phase=session.phase.phase if session else Phase.EVALUATE,
            chat_used=session.ledger.used if session else 0,
            chat_limit=session.ledger.limit if session else self._limit_for_next_chat or settings.token_limit_per_chat,
            context_tokens=session.context_tokens() if session else 0,
            context_budget=spec.history_budget,
            running_turn=self._running.id if self._running else None,
            pending_approvals=[
                ApprovalRequired(approval_id=approval_id, kind=request.kind, text=request.text)
                for approval_id, request in self._broker.pending().items()
            ],
            can_undo_compaction=session.can_undo_compaction if session else False,
        )

    async def _run(self, record: TurnRecord, session: ChatSession, text: str) -> None:
        try:
            await session.run_turn(text, emit=record.add, approvals=self._broker, cancel=self._cancel)
        except Exception:
            log.exception("turn %s crashed", record.id)
            await record.add(ErrorRaised(code="internal_error", message="Something went wrong inside Praxium."))
            await record.add(TurnFinished(status=TurnStatus.FAILED, message="The turn failed."))
        finally:
            self._running = None

    def _new_session(self, project: Project) -> ChatSession:
        settings = self.settings.load()
        spec = PROVIDERS[settings.provider]
        api_key = self.settings.api_key_for(spec.id)
        if not api_key:
            raise ApiError(400, "missing_api_key", f"Add your {spec.label} API key in Settings.")
        tools = [
            *make_file_tools(ProjectPaths(project.root)),
            make_shell_tool(project, self.command_env),
            make_skill_tool(self.skills),
        ]
        prompt = build_system_prompt(library=self.skills, os_name=self.os_name, shell_name=self.shell_name)
        return ChatSession(
            model=self.client_for(spec, api_key),
            spec=spec,
            tools=tools,
            system_prompt=prompt,
            token_limit=self._limit_for_next_chat or settings.token_limit_per_chat,
        )

    def _reset_chat(self) -> None:
        self._session = None
        self._turns.clear()
        self._limit_for_next_chat = None

    def _require_project(self) -> Project:
        if self.project is None:
            raise ApiError(409, "no_project", "Open a project folder first.")
        return self.project

    def _refuse_while_running(self) -> None:
        if self._running is not None:
            raise ApiError(409, "turn_running", "Wait for the agent to finish, or press Stop.")
        if self._compacting:
            raise ApiError(409, "compacting", "Wait for the compaction to finish.")
