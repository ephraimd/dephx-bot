import asyncio
from collections.abc import AsyncIterator, Callable
from urllib.parse import urlparse

from fastapi import APIRouter
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from dephx_bot.agent.approvals import ApprovalAnswer, Decision
from dephx_bot.agent.events import Compacted
from dephx_bot.chat.service import ChatService, ChatState
from dephx_bot.errors import ApiError
from dephx_bot.settings.project import ProjectView
from dephx_bot.tools import ToolError
from dephx_bot.tools.files import ProjectPaths

# SVG is left out on purpose: it can carry scripts.
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}


class OpenProject(BaseModel):
    path: str


class SendMessage(BaseModel):
    text: str


class Answer(BaseModel):
    decision: Decision
    feedback: str | None = None


class TokenLimit(BaseModel):
    limit: int


class Link(BaseModel):
    url: str


class Compact(BaseModel):
    keep: str | None = None


def chat_router(
    service: ChatService, *, pick_folder: Callable[[], str | None] | None, open_url: Callable[[str], bool]
) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.post("/project/open")
    async def open_project(body: OpenProject) -> ProjectView:
        return service.open_project(body.path)

    @router.post("/project/trust")
    async def trust_project() -> ProjectView:
        return service.trust_project()

    @router.post("/project/browse")
    async def browse() -> dict[str, str | None]:
        if pick_folder is None:
            raise ApiError(409, "no_window", "Folder picking needs the dephx-bot window. Type the path instead.")
        # The native dialog blocks, so keep it off the event loop.
        return {"path": await asyncio.to_thread(pick_folder)}

    @router.get("/project/image")
    async def project_image(path: str) -> FileResponse:
        if service.project is None:
            raise ApiError(409, "no_project", "Open a project folder first.")
        try:
            target = ProjectPaths(service.project.root).resolve(path)
        except ToolError as error:
            raise ApiError(400, "bad_path", str(error)) from error
        media_type = IMAGE_TYPES.get(target.suffix.lower())
        if media_type is None:
            raise ApiError(400, "not_an_image", f"{path} is not a supported image.")
        if not target.is_file():
            raise ApiError(404, "not_found", f"{path} does not exist.")
        return FileResponse(target, media_type=media_type)

    @router.post("/open-link")
    async def open_link(body: Link) -> dict[str, bool]:
        url = urlparse(body.url)
        if url.scheme not in ("http", "https") or not url.netloc:
            raise ApiError(400, "unsupported_link", "Only web links can be opened.")
        open_url(body.url)
        return {"ok": True}

    @router.get("/chat/state")
    async def state() -> ChatState:
        return service.state()

    @router.post("/chat/messages")
    async def send(body: SendMessage) -> dict[str, str]:
        return {"turn_id": await service.start_turn(body.text)}

    @router.get("/chat/turns/{turn_id}/events")
    async def turn_events(turn_id: str) -> StreamingResponse:
        service.turn(turn_id)  # fail with 404 before the stream starts

        async def frames() -> AsyncIterator[str]:
            async for event in service.events(turn_id):
                yield f"data: {event.model_dump_json()}\n\n"

        return StreamingResponse(frames(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @router.post("/chat/approvals/{approval_id}")
    async def answer(approval_id: str, body: Answer) -> dict[str, bool]:
        service.answer(approval_id, ApprovalAnswer(body.decision, body.feedback))
        return {"ok": True}

    @router.post("/chat/stop")
    async def stop() -> dict[str, bool]:
        service.stop()
        return {"ok": True}

    @router.put("/chat/token-limit")
    async def token_limit(body: TokenLimit) -> ChatState:
        service.set_token_limit(body.limit)
        return service.state()

    @router.post("/chat/compact")
    async def compact(body: Compact) -> Compacted:
        return await service.compact(body.keep)

    @router.post("/chat/compact/undo")
    async def undo_compaction() -> ChatState:
        return service.undo_compaction()

    @router.post("/chat/new")
    async def new_chat() -> dict[str, bool]:
        service.new_chat()
        return {"ok": True}

    return router
