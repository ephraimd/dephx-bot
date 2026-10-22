"""Settings: which provider to call, with which key and model.

Every provider here speaks the OpenAI Chat Completions format, so switching between
them changes only base_url, api_key and model. The rest of the agent never knows.

Pick one with the PROVIDER environment variable (default: deepseek):

    PROVIDER=praxiom uv run python main.py
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

# playground/malaria-agent/config.py -> project root is two folders up.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
VAULT_PATH = PROJECT_ROOT / ".config" / ".vault"
DEFAULT_PROVIDER = "deepseek"


@dataclass(frozen=True)
class Provider:
    base_url: str
    model: str
    key_name: str  # the variable holding the key, in the environment or the vault


PROVIDERS: dict[str, Provider] = {
    # "deepseek-chat" is now an alias of deepseek-flash; naming it directly avoids surprises.
    "deepseek": Provider("https://api.deepseek.com", "deepseek-flash", "DEEPSEEK_API_KEY"),
    "praxiom": Provider("https://cloud.praxiomai.io/v1", "praxiom", "PRAXIUM_API_KEY"),
}


@dataclass(frozen=True)
class Settings:
    provider: str
    base_url: str
    api_key: str
    model: str


def load_settings(provider_name: str | None = None) -> Settings:
    """Resolve the provider, then read its key from the environment or the vault file."""
    name = provider_name or os.environ.get("PROVIDER", DEFAULT_PROVIDER)
    provider = PROVIDERS.get(name)
    if provider is None:
        raise RuntimeError(f"Unknown provider '{name}'. Choose one of: {', '.join(PROVIDERS)}")

    vault = dotenv_values(VAULT_PATH) if VAULT_PATH.exists() else {}
    api_key = os.environ.get(provider.key_name) or vault.get(provider.key_name)
    if not api_key:
        raise RuntimeError(f"{provider.key_name} not found in the environment or {VAULT_PATH}")

    return Settings(name, provider.base_url, api_key, provider.model)
