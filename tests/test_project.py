import json
from pathlib import Path

import pytest

from dephx_bot.errors import ApiError
from dephx_bot.settings.project import open_project


def write_settings(root: Path, data: dict[str, object]) -> Path:
    path = root / ".dephx" / "settings.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def test_new_folder_needs_no_trust(tmp_path: Path) -> None:
    project = open_project(str(tmp_path))
    assert project.pending_trust == []
    assert project.view().name == tmp_path.name


def test_existing_allow_list_needs_trust(tmp_path: Path) -> None:
    write_settings(tmp_path, {"allowed_commands": ["pytest"]})
    project = open_project(str(tmp_path))

    assert project.pending_trust == ["pytest"]
    assert not project.is_allowed("pytest")

    project.trust()
    assert project.is_allowed("pytest")
    assert project.pending_trust == []


def test_allow_always_persists_and_keeps_other_keys(tmp_path: Path) -> None:
    path = write_settings(tmp_path, {"allowed_commands": [], "note": "keep me"})
    project = open_project(str(tmp_path))
    project.allow_always("npm test")

    assert project.is_allowed("npm test")
    assert json.loads(path.read_text()) == {"allowed_commands": ["npm test"], "note": "keep me"}


def test_allow_always_creates_the_settings_file(tmp_path: Path) -> None:
    open_project(str(tmp_path)).allow_always("pytest")
    saved = json.loads((tmp_path / ".dephx" / "settings.json").read_text())
    assert saved == {"allowed_commands": ["pytest"]}


def test_disk_edit_after_open_has_no_effect(tmp_path: Path) -> None:
    path = write_settings(tmp_path, {"allowed_commands": ["pytest"]})
    project = open_project(str(tmp_path))
    project.trust()

    path.write_text(json.dumps({"allowed_commands": ["pytest", "rm -rf /"]}))
    assert not project.is_allowed("rm -rf /")


def test_missing_folder_rejected(tmp_path: Path) -> None:
    with pytest.raises(ApiError) as error:
        open_project(str(tmp_path / "nope"))
    assert error.value.code == "not_a_folder"


def test_unreadable_settings_rejected(tmp_path: Path) -> None:
    (tmp_path / ".dephx").mkdir()
    (tmp_path / ".dephx" / "settings.json").write_text("{not json")
    with pytest.raises(ApiError) as error:
        open_project(str(tmp_path))
    assert error.value.code == "bad_project_settings"
