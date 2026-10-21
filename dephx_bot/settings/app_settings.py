from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ValidationError

from dephx_bot.errors import ApiError
from dephx_bot.providers import CUSTOM_PROVIDER_ID, PROVIDERS, ProviderSpec, custom_provider
from dephx_bot.settings import write_text_atomic

DEFAULT_PROVIDER = "deepseek"
DEFAULT_TOKEN_LIMIT_PER_CHAT = 300_000
KEY_PREFIX_LENGTH = 8
# A custom model needs room for the system prompt and some working history besides its output.
MIN_CUSTOM_HISTORY_TOKENS = 8_000


class CustomProvider(BaseModel):
    base_url: str
    model: str
    context_window: int = 128_000
    max_output_tokens: int = 8_192


class AppSettings(BaseModel):
    provider: str = DEFAULT_PROVIDER
    api_keys: dict[str, str] = {}
    token_limit_per_chat: int = DEFAULT_TOKEN_LIMIT_PER_CHAT
    custom: CustomProvider | None = None


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
    custom: CustomProvider | None


class AppSettingsStore:
    def __init__(self, path: Path, env_api_keys: Mapping[str, str]) -> None:
        self.path = path
        self.env_api_keys = env_api_keys

    def load(self) -> AppSettings:
        if not self.path.exists():
            return AppSettings()
        return AppSettings.model_validate_json(self.path.read_text(encoding="utf-8"))

    def provider_spec(self, provider_id: str | None = None) -> ProviderSpec:
        """The provider in use (or the one named), including a custom endpoint the user configured."""
        settings = self.load()
        chosen = provider_id or settings.provider
        if chosen == CUSTOM_PROVIDER_ID:
            if settings.custom is None:
                raise ApiError(400, "custom_provider_incomplete", "Enter the custom provider's address and model.")
            c = settings.custom
            return custom_provider(c.base_url, c.model, c.context_window, c.max_output_tokens)
        return PROVIDERS[chosen]

    def api_key_for(self, provider_id: str) -> str | None:
        spec = PROVIDERS.get(provider_id)
        env_key = self.env_api_keys.get(spec.env_key) if spec and spec.env_key else None
        return env_key or self.load().api_keys.get(provider_id)

    def view(self) -> SettingsView:
        settings = self.load()
        keys = {}
        for provider_id in [*PROVIDERS, CUSTOM_PROVIDER_ID]:
            key = self.api_key_for(provider_id)
            keys[provider_id] = KeyStatus(is_set=bool(key), prefix=key[:KEY_PREFIX_LENGTH] if key else None)
        options = [ProviderOption(id=p.id, label=p.label, min_token_limit=p.min_token_limit()) for p in PROVIDERS.values()]
        custom_minimum = self.provider_spec(CUSTOM_PROVIDER_ID).min_token_limit() if settings.custom else 0
        options.append(
            ProviderOption(id=CUSTOM_PROVIDER_ID, label="Custom (any OpenAI-compatible API)", min_token_limit=custom_minimum)
        )
        return SettingsView(
            provider=settings.provider,
            providers=options,
            keys=keys,
            token_limit_per_chat=settings.token_limit_per_chat,
            custom=settings.custom,
        )

    def update(
        self,
        *,
        provider: str | None,
        api_key: str | None,
        token_limit_per_chat: int | None,
        custom: dict[str, Any] | None = None,
    ) -> SettingsView:
        settings = self.load()
        if provider is not None:
            if provider not in PROVIDERS and provider != CUSTOM_PROVIDER_ID:
                raise ApiError(400, "unknown_provider", f"Unknown provider '{provider}'.")
            settings.provider = provider
        if custom is not None:
            settings.custom = _checked_custom(custom)
        if api_key is not None:
            if not api_key.strip():
                raise ApiError(400, "empty_api_key", "The API key is empty.")
            settings.api_keys[settings.provider] = api_key.strip()
        if token_limit_per_chat is not None:
            settings.token_limit_per_chat = token_limit_per_chat

        if settings.provider == CUSTOM_PROVIDER_ID and settings.custom is None:
            raise ApiError(400, "custom_provider_incomplete", "Enter the custom provider's address and model.")
        if settings.provider == CUSTOM_PROVIDER_ID and settings.custom is not None:
            c = settings.custom
            spec = custom_provider(c.base_url, c.model, c.context_window, c.max_output_tokens)
        else:
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


def _checked_custom(raw: dict[str, Any]) -> CustomProvider:
    try:
        custom = CustomProvider.model_validate(raw)
    except ValidationError as error:
        raise ApiError(400, "invalid_custom_provider", "The custom provider's details are incomplete.") from error
    url = urlparse(custom.base_url.strip())
    if url.scheme not in ("http", "https") or not url.netloc:
        raise ApiError(400, "invalid_custom_provider", "The address must start with http:// or https://.")
    if not custom.model.strip():
        raise ApiError(400, "invalid_custom_provider", "Enter the model's name, as the provider spells it.")
    if custom.context_window - custom.max_output_tokens < MIN_CUSTOM_HISTORY_TOKENS:
        raise ApiError(
            400,
            "invalid_custom_provider",
            f"The context window must be at least {MIN_CUSTOM_HISTORY_TOKENS:,} tokens larger than the maximum output.",
        )
    return CustomProvider(
        base_url=custom.base_url.strip(),
        model=custom.model.strip(),
        context_window=custom.context_window,
        max_output_tokens=custom.max_output_tokens,
    )
