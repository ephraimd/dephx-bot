import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from dephx_bot.errors import ApiError
from dephx_bot.settings import write_text_atomic

PROJECT_SETTINGS_DIR = ".dephx"
PROJECT_SETTINGS_FILE = "settings.json"


class ProjectView(BaseModel):
    name: str
    root: str
    needs_trust: list[str]


class Project:
    """An open project folder and the commands the user allowed to run without asking.

    The allow-list is read from disk once, at open, and only changes through the user after
    that: an approval in the UI, or a settings change the user applied. A command that
    rewrites the settings file therefore cannot grant itself anything.
    """

    def __init__(self, root: Path, pending_trust: list[str]) -> None:
        self.root = root
        self.name = root.name
        self.pending_trust = pending_trust
        self._allowed: set[str] = set()

    @property
    def settings_path(self) -> Path:
        return self.root / PROJECT_SETTINGS_DIR / PROJECT_SETTINGS_FILE

    def is_allowed(self, command: str) -> bool:
        return command in self._allowed

    def trust(self) -> None:
        self._allowed.update(self.pending_trust)
        self.pending_trust = []

    def allow_always(self, command: str) -> None:
        data = _read_settings(self.settings_path)
        allowed = data.setdefault("allowed_commands", [])
        if command not in allowed:
            allowed.append(command)
        write_text_atomic(self.settings_path, json.dumps(data, indent=2) + "\n")
        self._allowed.add(command)

    def allowed_commands(self) -> list[str]:
        return list(_read_settings(self.settings_path).get("allowed_commands", []))

    def set_allowed_commands(self, commands: list[str]) -> None:
        """Replace the allow-list with one the user approved in full."""
        data = _read_settings(self.settings_path)
        data["allowed_commands"] = commands
        write_text_atomic(self.settings_path, json.dumps(data, indent=2) + "\n")
        self._allowed = set(commands)

    def view(self) -> ProjectView:
        return ProjectView(name=self.name, root=str(self.root), needs_trust=self.pending_trust)


def open_project(path: str) -> Project:
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise ApiError(400, "not_a_folder", f"{path} is not a folder.")
    settings = _read_settings(root / PROJECT_SETTINGS_DIR / PROJECT_SETTINGS_FILE)
    return Project(root, pending_trust=list(settings.get("allowed_commands", [])))


def _read_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ApiError(400, "bad_project_settings", f"Could not read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ApiError(400, "bad_project_settings", f"{path} must contain a JSON object.")
    return data
