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
