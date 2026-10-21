from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from praxium.app import build_api
from praxium.config import AppConfig

TOKEN = "t0k"
HOST = "127.0.0.1:9999"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<p>placeholder</p>")
    config = AppConfig(
        dev=True,
        settings_path=tmp_path / "settings.json",
        static_dir=static_dir,
        skills_dir=tmp_path / "skills",
        env_api_keys={},
        os_name="macOS",
        shell_name="/bin/sh",
    )
    return TestClient(build_api(config, token=TOKEN, host=HOST), base_url=f"http://{HOST}")


def test_api_requires_token(client: TestClient) -> None:
    assert client.get("/api/health").status_code == 401


def test_api_accepts_token(client: TestClient) -> None:
    response = client.get("/api/health", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_wrong_token_rejected(client: TestClient) -> None:
    response = client.get("/api/health", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


def test_wrong_host_rejected_even_with_token(client: TestClient) -> None:
    response = client.get("/api/health", headers={**AUTH, "Host": "evil.example:9999"})
    assert response.status_code == 421


def test_static_needs_no_token(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "placeholder" in response.text


def test_error_contract_shape(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.json() == {
        "error": {"code": "unauthorized", "message": "Missing or invalid launch token."}
    }
