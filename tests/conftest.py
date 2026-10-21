from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from praxium.app import build_api
from praxium.config import AppConfig

TOKEN = "t0k"
HOST = "127.0.0.1:9999"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def config(tmp_path: Path) -> AppConfig:
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<p>placeholder</p>")
    return AppConfig(
        dev=True,
        settings_path=tmp_path / "config" / "settings.json",
        static_dir=static_dir,
        skills_dir=tmp_path / "skills",
        env_api_keys={},
        os_name="macOS",
        shell_name="/bin/sh",
    )


@pytest.fixture
def client(config: AppConfig) -> TestClient:
    return TestClient(build_api(config, token=TOKEN, host=HOST), base_url=f"http://{HOST}")
