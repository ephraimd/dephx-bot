import argparse
import platform
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_path

PACKAGE_DIR = Path(__file__).resolve().parent
DEV_PORT = 8765
# Replaced by the provider contract's env_key values once providers exist (Task 2).
API_KEY_ENV_VARS = ("PRAXIUM_API_KEY", "DEEPSEEK_API_KEY")


@dataclass(frozen=True)
class AppConfig:
    dev: bool
    settings_path: Path
    static_dir: Path
    skills_dir: Path
    env_api_keys: dict[str, str]
    os_name: str
    shell_name: str


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
        env_api_keys={name: env[name] for name in API_KEY_ENV_VARS if env.get(name)},
        os_name={"Darwin": "macOS"}.get(system, system),
        shell_name="cmd.exe" if system == "Windows" else "/bin/sh",
    )
