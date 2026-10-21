import argparse
import os
import platform
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_path

from praxium.providers import PROVIDERS

PACKAGE_DIR = Path(__file__).resolve().parent
DEV_PORT = 8765


@dataclass(frozen=True)
class AppConfig:
    dev: bool
    settings_path: Path
    static_dir: Path
    skills_dir: Path
    env_api_keys: dict[str, str]
    os_name: str
    shell_name: str
    command_env: dict[str, str]


def load_config(argv: Sequence[str], env: Mapping[str, str]) -> AppConfig:
    parser = argparse.ArgumentParser(prog="praxium")
    parser.add_argument(
        "--dev",
        action="store_true",
        help=f"serve on 127.0.0.1:{DEV_PORT} without a window, for UI development",
    )
    args = parser.parse_args(argv)

    system = platform.system()
    return AppConfig(
        dev=args.dev,
        settings_path=user_config_path("praxium") / "settings.json",
        static_dir=PACKAGE_DIR / "static",
        skills_dir=PACKAGE_DIR / "skills" / "senior-dephx",
        env_api_keys={p.env_key: env[p.env_key] for p in PROVIDERS.values() if env.get(p.env_key)},
        os_name={"Darwin": "macOS"}.get(system, system),
        shell_name="cmd.exe" if system == "Windows" else "/bin/sh",
        command_env=command_env(env, own_prefix=sys.prefix, pathsep=os.pathsep),
    )


def command_env(env: Mapping[str, str], *, own_prefix: str, pathsep: str) -> dict[str, str]:
    """The environment for the agent's commands: ours, minus Praxium's own virtualenv.

    `uv run praxium` puts Praxium's .venv first on PATH, so without this the agent's
    `python3` and `pip install` would use and change Praxium's environment, not the project's.
    """
    cleaned = dict(env)
    if cleaned.get("VIRTUAL_ENV") != own_prefix:
        return cleaned
    del cleaned["VIRTUAL_ENV"]
    inside = (own_prefix + "/", own_prefix + "\\")
    kept = [entry for entry in cleaned.get("PATH", "").split(pathsep) if entry != own_prefix and not entry.startswith(inside)]
    cleaned["PATH"] = pathsep.join(kept)
    return cleaned
