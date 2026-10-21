from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from dephx_bot.settings.app_settings import AppSettingsStore, SettingsView


class SettingsUpdate(BaseModel):
    provider: str | None = None
    api_key: str | None = None
    token_limit_per_chat: int | None = None
    custom: dict[str, Any] | None = None


def settings_router(store: AppSettingsStore) -> APIRouter:
    router = APIRouter(prefix="/api/settings")

    @router.get("")
    async def get_settings() -> SettingsView:
        return store.view()

    @router.put("")
    async def update_settings(update: SettingsUpdate) -> SettingsView:
        return store.update(
            provider=update.provider,
            api_key=update.api_key,
            token_limit_per_chat=update.token_limit_per_chat,
            custom=update.custom,
        )

    return router
