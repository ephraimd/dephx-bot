"""Composition root: everything Praxium is made of is assembled here and nowhere else."""

import os
import socket
import sys
import threading
import time

import uvicorn
import webview
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from praxium.config import DEV_PORT, AppConfig, load_config
from praxium.errors import install_error_handler
from praxium.security import LaunchGuard, new_launch_token
from praxium.settings.app_settings import AppSettingsStore
from praxium.settings.routes import settings_router

SERVER_START_TIMEOUT_SECONDS = 10.0


def build_api(config: AppConfig, *, token: str, host: str) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    install_error_handler(app)

    @app.get("/api/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    settings = AppSettingsStore(config.settings_path, config.env_api_keys)
    app.include_router(settings_router(settings))

    # Mounted last: "/" would otherwise swallow every /api route.
    app.mount("/", StaticFiles(directory=config.static_dir, html=True), name="ui")
    app.add_middleware(LaunchGuard, token=token, host=host)
    return app


def main() -> None:
    config = load_config(sys.argv[1:], os.environ)
    sock = _bind_localhost(DEV_PORT if config.dev else 0)
    port = sock.getsockname()[1]
    token = new_launch_token()
    app = build_api(config, token=token, host=f"127.0.0.1:{port}")
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    # The fragment is never sent to the server, so the token stays out of request logs.
    url = f"http://127.0.0.1:{port}/#token={token}"

    if config.dev:
        print(f"Praxium dev server: {url}", flush=True)
        server.run(sockets=[sock])
        return

    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    _wait_until_started(server)
    webview.create_window("Praxium", url, width=1200, height=800)
    webview.start()
    server.should_exit = True
    thread.join(timeout=5)


def _bind_localhost(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", port))
    return sock


def _wait_until_started(server: uvicorn.Server) -> None:
    deadline = time.monotonic() + SERVER_START_TIMEOUT_SECONDS
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("The local server did not start in time.")
        time.sleep(0.05)
