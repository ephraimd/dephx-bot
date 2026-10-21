from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel

from praxium.errors import ApiError
from praxium.providers import PROVIDERS
from praxium.settings import write_text_atomic

DEFAULT_PROVIDER = "deepseek"
DEFAULT_TOKEN_LIMIT_PER_CHAT = 300_000
KEY_PREFIX_LENGTH = 8


class AppSettings(BaseModel):
    provider: str = DEFAULT_PROVIDER
    api_keys: dict[str, str] = {}
    token_limit_per_chat: int = DEFAULT_TOKEN_LIMIT_PER_CHAT


class KeyStatus(BaseModel):
    is_set: bool
    prefix: str | None


class ProviderOption(BaseModel):
    id: str
    label: str
    min_token_limit: int


class SettingsView(BaseModel):
    """What the UI may see. API keys never leave this module, only whether one is set."""

    provider: str
    providers: list[ProviderOption]
    keys: dict[str, KeyStatus]
    token_limit_per_chat: int


class AppSettingsStore:
    def __init__(self, path: Path, env_api_keys: Mapping[str, str]) -> None:
        self.path = path
        self.env_api_keys = env_api_keys

    def load(self) -> AppSettings:
        if not self.path.exists():
            return AppSettings()
        return AppSettings.model_validate_json(self.path.read_text(encoding="utf-8"))

    def api_key_for(self, provider_id: str) -> str | None:
        env_key = self.env_api_keys.get(PROVIDERS[provider_id].env_key)
        return env_key or self.load().api_keys.get(provider_id)

    def view(self) -> SettingsView:
        settings = self.load()
        keys = {}
        for provider_id in PROVIDERS:
            key = self.api_key_for(provider_id)
            keys[provider_id] = KeyStatus(is_set=bool(key), prefix=key[:KEY_PREFIX_LENGTH] if key else None)
        return SettingsView(
            provider=settings.provider,
            providers=[
                ProviderOption(id=p.id, label=p.label, min_token_limit=p.min_token_limit())
                for p in PROVIDERS.values()
            ],
            keys=keys,
            token_limit_per_chat=settings.token_limit_per_chat,
        )

    def update(
        self, *, provider: str | None, api_key: str | None, token_limit_per_chat: int | None
    ) -> SettingsView:
        settings = self.load()
        if provider is not None:
            if provider not in PROVIDERS:
                raise ApiError(400, "unknown_provider", f"Unknown provider '{provider}'.")
            settings.provider = provider
        if api_key is not None:
            if not api_key.strip():
                raise ApiError(400, "empty_api_key", "The API key is empty.")
            settings.api_keys[settings.provider] = api_key.strip()
        if token_limit_per_chat is not None:
            settings.token_limit_per_chat = token_limit_per_chat

        spec = PROVIDERS[settings.provider]
        if settings.token_limit_per_chat < spec.min_token_limit():
            raise ApiError(
                400,
                "token_limit_too_low",
                f"The token limit must be at least {spec.min_token_limit():,} for {spec.label}, "
                "the size of one full request.",
            )

        write_text_atomic(self.path, settings.model_dump_json(indent=2), mode=0o600)
        return self.view()
