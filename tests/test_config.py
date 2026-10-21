import os
import sys
from pathlib import Path

import pytest

from praxium.config import command_env


def test_drops_our_own_virtualenv_from_commands() -> None:
    env = {"VIRTUAL_ENV": "/app/.venv", "PATH": "/app/.venv/bin:/usr/local/bin:/usr/bin", "HOME": "/home/me"}
    cleaned = command_env(env, own_prefix="/app/.venv", pathsep=":")
    assert cleaned == {"PATH": "/usr/local/bin:/usr/bin", "HOME": "/home/me"}


def test_drops_our_virtualenv_on_windows() -> None:
    env = {"VIRTUAL_ENV": r"C:\app\.venv", "PATH": r"C:\app\.venv\Scripts;C:\Windows\system32"}
    cleaned = command_env(env, own_prefix=r"C:\app\.venv", pathsep=";")
    assert cleaned == {"PATH": r"C:\Windows\system32"}


def test_leaves_someone_elses_virtualenv_alone() -> None:
    env = {"VIRTUAL_ENV": "/work/.venv", "PATH": "/work/.venv/bin:/usr/bin"}
    assert command_env(env, own_prefix="/opt/praxium", pathsep=":") == env


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need admin rights on Windows")
def test_matches_our_virtualenv_through_a_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real-venv"
    (real / "bin").mkdir(parents=True)
    link = tmp_path / "linked-venv"
    os.symlink(real, link)
    env = {"VIRTUAL_ENV": str(link), "PATH": f"{link}/bin:/usr/bin"}
    assert command_env(env, own_prefix=str(real), pathsep=":") == {"PATH": "/usr/bin"}
