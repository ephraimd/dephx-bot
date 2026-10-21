import json
import sys
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from praxium.app import build_api
from praxium.config import AppConfig
from praxium.model import ModelClient, ModelReply
from praxium.providers import ProviderSpec
from tests.conftest import AUTH, HOST, TOKEN
from tests.scripted_model import ScriptedModel, call, text

PASSING_CHECK = f'"{sys.executable}" -c "print(1)"'


class Models:
    """Hands each new chat the next scripted model and records which provider it was for."""

    def __init__(self, *scripts: list[ModelReply]) -> None:
        self.scripts = list(scripts)
        self.providers: list[str] = []

    def __call__(self, spec: ProviderSpec, api_key: str) -> ModelClient:
        self.providers.append(spec.id)
        return ScriptedModel(list(self.scripts.pop(0)))


class Links:
    def __init__(self) -> None:
        self.opened: list[str] = []

    def __call__(self, url: str) -> bool:
        self.opened.append(url)
        return True


@pytest.fixture
def keyed_config(config: AppConfig) -> AppConfig:
    return replace(config, env_api_keys={"DEEPSEEK_API_KEY": "sk-test", "PRAXIUM_API_KEY": "px-test"})


def make_client(config: AppConfig, models: Models, links: Links | None = None) -> TestClient:
    app = build_api(
        config, token=TOKEN, host=HOST, client_for=models, open_url=links or Links(), pick_folder=None
    )
    return TestClient(app, base_url=f"http://{HOST}")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "a.py").write_text("print('a')\n")
    return root


def post(client: TestClient, path: str, body: dict[str, Any] | None = None) -> Any:
    response = client.post(path, headers=AUTH, json=body or {})
    assert response.status_code == 200, response.text
    return response.json()


def events(client: TestClient, turn_id: str, until: str = "turn_finished") -> Iterator[dict[str, Any]]:
    with client.stream("GET", f"/api/chat/turns/{turn_id}/events", headers=AUTH) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("data: "):
                event = json.loads(line[len("data: "):])
                yield event
                if event["type"] == until:
                    return


def test_turn_streams_events_and_finishes(keyed_config: AppConfig, project: Path) -> None:
    with make_client(keyed_config, Models([text("Which change do you want?")])) as client:
        post(client, "/api/project/open", {"path": str(project)})
        turn_id = post(client, "/api/chat/messages", {"text": "hi"})["turn_id"]

        received = list(events(client, turn_id))

    assert [e["type"] for e in received] == ["token", "usage", "turn_finished"]
    assert received[-1]["status"] == "waiting_for_user"


def test_message_without_project_explains(keyed_config: AppConfig) -> None:
    with make_client(keyed_config, Models()) as client:
        response = client.post("/api/chat/messages", headers=AUTH, json={"text": "hi"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "no_project"


def test_message_without_key_explains(config: AppConfig, project: Path) -> None:
    with make_client(config, Models()) as client:
        post(client, "/api/project/open", {"path": str(project)})
        response = client.post("/api/chat/messages", headers=AUTH, json={"text": "hi"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "missing_api_key"
    assert "DeepSeek" in response.json()["error"]["message"]


def test_late_subscriber_gets_full_replay_and_can_answer(keyed_config: AppConfig, project: Path) -> None:
    script = [
        call("advance_phase", {"to": "ideate", "summary": "looked"}),
        call("advance_phase", {"to": "build", "summary": "1. run it"}),
        call("run_command", {"command": PASSING_CHECK}),
        text("Ran it."),
        text("Still in build."),
    ]
    with make_client(keyed_config, Models(script)) as client:
        post(client, "/api/project/open", {"path": str(project)})
        turn_id = post(client, "/api/chat/messages", {"text": "run it"})["turn_id"]

        plan = list(events(client, turn_id, until="approval_required"))[-1]
        busy = client.post("/api/chat/messages", headers=AUTH, json={"text": "again"})
        assert busy.status_code == 409 and busy.json()["error"]["code"] == "turn_running"
        assert client.get("/api/chat/state", headers=AUTH).json()["pending_approvals"][0]["kind"] == "plan"
        post(client, f"/api/chat/approvals/{plan['approval_id']}", {"decision": "approve"})

        # A fresh subscriber (a reloaded window) replays from the start, plan question included,
        # and then sees the next question arrive.
        replayed = []
        for event in events(client, turn_id):
            replayed.append(event)
            if event["type"] == "approval_required" and event["kind"] == "command":
                break
        assert any(e["type"] == "approval_required" and e["kind"] == "plan" for e in replayed)
        command = replayed[-1]
        post(client, f"/api/chat/approvals/{command['approval_id']}", {"decision": "run_once"})

        finished = list(events(client, turn_id))[-1]

    assert finished["type"] == "turn_finished"
    assert finished["status"] == "incomplete"


def test_stop_ends_a_turn_waiting_for_approval(keyed_config: AppConfig, project: Path) -> None:
    script = [
        call("advance_phase", {"to": "ideate", "summary": "looked"}),
        call("advance_phase", {"to": "build", "summary": "1. run it"}),
    ]
    with make_client(keyed_config, Models(script)) as client:
        post(client, "/api/project/open", {"path": str(project)})
        turn_id = post(client, "/api/chat/messages", {"text": "go"})["turn_id"]
        list(events(client, turn_id, until="approval_required"))

        post(client, "/api/chat/stop")
        finished = list(events(client, turn_id))[-1]
        state = client.get("/api/chat/state", headers=AUTH).json()

    assert finished["status"] == "stopped"
    assert state["running_turn"] is None


def test_settings_change_applies_to_next_chat_only(keyed_config: AppConfig, project: Path) -> None:
    models = Models([text("first")], [text("second")])
    with make_client(keyed_config, models) as client:
        post(client, "/api/project/open", {"path": str(project)})
        list(events(client, post(client, "/api/chat/messages", {"text": "hi"})["turn_id"]))

        client.put("/api/settings", headers=AUTH, json={"provider": "praxiom"})
        assert client.get("/api/chat/state", headers=AUTH).json()["provider"] == "deepseek"

        post(client, "/api/chat/new")
        list(events(client, post(client, "/api/chat/messages", {"text": "hi"})["turn_id"]))
        assert client.get("/api/chat/state", headers=AUTH).json()["provider"] == "praxiom"

    assert models.providers == ["deepseek", "praxiom"]


def test_settings_writable_while_turn_running(keyed_config: AppConfig, project: Path) -> None:
    script = [call("advance_phase", {"to": "ideate", "summary": "x"}), call("advance_phase", {"to": "build", "summary": "plan"})]
    with make_client(keyed_config, Models(script)) as client:
        post(client, "/api/project/open", {"path": str(project)})
        turn_id = post(client, "/api/chat/messages", {"text": "go"})["turn_id"]
        list(events(client, turn_id, until="approval_required"))

        response = client.put("/api/settings", headers=AUTH, json={"token_limit_per_chat": 500_000})
        post(client, "/api/chat/stop")

    assert response.status_code == 200


def test_chat_token_limit_is_checked_against_the_chat_provider(keyed_config: AppConfig, project: Path) -> None:
    with make_client(keyed_config, Models([text("hi")])) as client:
        post(client, "/api/project/open", {"path": str(project)})
        list(events(client, post(client, "/api/chat/messages", {"text": "hi"})["turn_id"]))

        too_low = client.put("/api/chat/token-limit", headers=AUTH, json={"limit": 70_000})
        ok = client.put("/api/chat/token-limit", headers=AUTH, json={"limit": 400_000})
        state = client.get("/api/chat/state", headers=AUTH).json()

    assert too_low.json()["error"]["code"] == "token_limit_too_low"
    assert ok.status_code == 200
    assert state["chat_limit"] == 400_000


def test_trust_flow(keyed_config: AppConfig, project: Path) -> None:
    (project / ".praxium").mkdir()
    (project / ".praxium" / "settings.json").write_text('{"allowed_commands": ["pytest"]}')
    with make_client(keyed_config, Models()) as client:
        opened = post(client, "/api/project/open", {"path": str(project)})
        trusted = post(client, "/api/project/trust")
    assert opened["needs_trust"] == ["pytest"]
    assert trusted["needs_trust"] == []


def test_browse_without_window_says_so(keyed_config: AppConfig) -> None:
    with make_client(keyed_config, Models()) as client:
        response = client.post("/api/project/browse", headers=AUTH)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "no_window"


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [("../x.png", 400, "bad_path"), (".git/a.png", 400, "bad_path"), ("a.py", 400, "not_an_image"), ("missing.png", 404, "not_found")],
)
def test_image_endpoint_applies_path_rule(keyed_config: AppConfig, project: Path, path: str, status: int, code: str) -> None:
    with make_client(keyed_config, Models()) as client:
        post(client, "/api/project/open", {"path": str(project)})
        response = client.get("/api/project/image", headers=AUTH, params={"path": path})
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


def test_image_endpoint_serves_project_images(keyed_config: AppConfig, project: Path) -> None:
    (project / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    with make_client(keyed_config, Models()) as client:
        post(client, "/api/project/open", {"path": str(project)})
        response = client.get("/api/project/image", headers=AUTH, params={"path": "chart.png"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "https://"])
def test_open_link_rejects_non_http(keyed_config: AppConfig, url: str) -> None:
    links = Links()
    with make_client(keyed_config, Models(), links) as client:
        response = client.post("/api/open-link", headers=AUTH, json={"url": url})
    assert response.json()["error"]["code"] == "unsupported_link"
    assert links.opened == []


def test_open_link_opens_http_in_system_browser(keyed_config: AppConfig) -> None:
    links = Links()
    with make_client(keyed_config, Models(), links) as client:
        post(client, "/api/open-link", {"url": "https://who.int/malaria"})
    assert links.opened == ["https://who.int/malaria"]



def chat_with_history(client: TestClient, project: Path) -> None:
    post(client, "/api/project/open", {"path": str(project)})
    for text_ in ("first", "second", "third"):
        list(events(client, post(client, "/api/chat/messages", {"text": text_})["turn_id"]))


def test_compact_and_undo_through_the_api(keyed_config: AppConfig, project: Path) -> None:
    script = [text("one"), text("two"), text("three"), text("Summary notes.")]
    with make_client(keyed_config, Models(script)) as client:
        chat_with_history(client, project)

        compacted = post(client, "/api/chat/compact", {"keep": "Use SQLite"})
        state = client.get("/api/chat/state", headers=AUTH).json()
        undone = post(client, "/api/chat/compact/undo")

    assert compacted["type"] == "compacted" and compacted["automatic"] is False
    assert "Use SQLite" in compacted["summary"]
    assert state["can_undo_compaction"] is True
    assert undone["can_undo_compaction"] is False


def test_compact_with_nothing_to_compact_explains(keyed_config: AppConfig, project: Path) -> None:
    with make_client(keyed_config, Models()) as client:
        post(client, "/api/project/open", {"path": str(project)})
        response = client.post("/api/chat/compact", headers=AUTH, json={})
        undo = client.post("/api/chat/compact/undo", headers=AUTH)
    assert response.status_code == 409 and response.json()["error"]["code"] == "nothing_to_compact"
    assert undo.status_code == 409 and undo.json()["error"]["code"] == "nothing_to_undo"


def test_compact_is_refused_while_a_turn_runs(keyed_config: AppConfig, project: Path) -> None:
    script = [call("advance_phase", {"to": "ideate", "summary": "x"}), call("advance_phase", {"to": "build", "summary": "plan"})]
    with make_client(keyed_config, Models(script)) as client:
        post(client, "/api/project/open", {"path": str(project)})
        turn_id = post(client, "/api/chat/messages", {"text": "go"})["turn_id"]
        list(events(client, turn_id, until="approval_required"))
        response = client.post("/api/chat/compact", headers=AUTH, json={})
        post(client, "/api/chat/stop")
    assert response.status_code == 409 and response.json()["error"]["code"] == "turn_running"
