"""Composition root: everything Praxium is made of is assembled here and nowhere else."""

import os
import socket
import sys
import threading
import time
import webbrowser
from collections.abc import Callable

import uvicorn
import webview
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from praxium.chat.routes import chat_router
from praxium.chat.service import ChatService, ClientFor
from praxium.config import DEV_PORT, AppConfig, load_config
from praxium.errors import install_error_handler
from praxium.providers import ClientPool
from praxium.security import LaunchGuard, new_launch_token
from praxium.settings.app_settings import AppSettingsStore
from praxium.settings.routes import settings_router
from praxium.tools.skills import SkillLibrary

SERVER_START_TIMEOUT_SECONDS = 10.0


def build_api(
    config: AppConfig,
    *,
    token: str,
    host: str,
    client_for: ClientFor | None = None,
    pick_folder: Callable[[], str | None] | None = None,
    open_url: Callable[[str], bool] = webbrowser.open,
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    install_error_handler(app)

    @app.get("/api/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    settings = AppSettingsStore(config.settings_path, config.env_api_keys)
    chat = ChatService(
        settings=settings,
        skills=SkillLibrary(config.skills_dir),
        client_for=client_for or ClientPool().get,
        os_name=config.os_name,
        shell_name=config.shell_name,
        command_env=config.command_env,
    )
    app.include_router(settings_router(settings))
    app.include_router(chat_router(chat, pick_folder=pick_folder, open_url=open_url))

    # Mounted last: "/" would otherwise swallow every /api route.
    app.mount("/", StaticFiles(directory=config.static_dir, html=True), name="ui")
    app.add_middleware(LaunchGuard, token=token, host=host)
    return app


def main() -> None:
    config = load_config(sys.argv[1:], os.environ)
    sock = _bind_localhost(DEV_PORT if config.dev else 0)
    port = sock.getsockname()[1]
    token = new_launch_token()
    host = f"127.0.0.1:{port}"
    # The fragment is never sent to the server, so the token stays out of request logs.
    url = f"http://{host}/#token={token}"

    if config.dev:
        server = uvicorn.Server(uvicorn.Config(build_api(config, token=token, host=host), log_level="warning"))
        print(f"Praxium dev server: {url}", flush=True)
        server.run(sockets=[sock])
        return

    # Created before the server so the folder picker can use it; it opens at webview.start().
    window = webview.create_window("Praxium", url, width=1200, height=800, min_size=(720, 560))
    if window is None:
        raise RuntimeError("pywebview could not create a window.")
    app = build_api(config, token=token, host=host, pick_folder=lambda: _pick_folder(window))
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    _wait_until_started(server)
    webview.start()
    server.should_exit = True
    thread.join(timeout=5)


def _pick_folder(window: webview.Window) -> str | None:
    chosen = window.create_file_dialog(webview.FileDialog.FOLDER)
    return chosen[0] if chosen else None


def _bind_localhost(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Lets a restart reuse the dev port while old connections finish closing. Not on Windows,
    # where the same option would let another program take over the port.
    if sys.platform != "win32":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    return sock


def _wait_until_started(server: uvicorn.Server) -> None:
    deadline = time.monotonic() + SERVER_START_TIMEOUT_SECONDS
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("The local server did not start in time.")
        time.sleep(0.05)
