import stat
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dephx_bot.errors import ApiError
from dephx_bot.settings.app_settings import AppSettingsStore, KeyStatus
from tests.conftest import AUTH


@pytest.fixture
def store(tmp_path: Path) -> AppSettingsStore:
    return AppSettingsStore(tmp_path / "config" / "settings.json", env_api_keys={})


def test_defaults_when_no_file(store: AppSettingsStore) -> None:
    view = store.view()
    assert view.provider == "deepseek"
    assert view.token_limit_per_chat == 300_000
    assert view.keys["praxiom"] == KeyStatus(is_set=False, prefix=None)


def test_view_never_contains_key(store: AppSettingsStore) -> None:
    store.update(provider="praxiom", api_key="px_live_abcdefghijk", token_limit_per_chat=None)
    view = store.view()
    assert view.keys["praxiom"] == KeyStatus(is_set=True, prefix="px_live_")
    assert "abcdefghijk" not in view.model_dump_json()


def test_key_applies_to_the_provider_being_saved(store: AppSettingsStore) -> None:
    store.update(provider="praxiom", api_key="px_live_one", token_limit_per_chat=None)
    store.update(provider="deepseek", api_key="sk-two", token_limit_per_chat=None)
    assert store.api_key_for("praxiom") == "px_live_one"
    assert store.api_key_for("deepseek") == "sk-two"


def test_settings_survive_a_new_store(tmp_path: Path, store: AppSettingsStore) -> None:
    store.update(provider="praxiom", api_key="px_live_abc", token_limit_per_chat=100_000)
    reopened = AppSettingsStore(tmp_path / "config" / "settings.json", env_api_keys={})
    assert reopened.view().provider == "praxiom"
    assert reopened.view().token_limit_per_chat == 100_000
    assert reopened.api_key_for("praxiom") == "px_live_abc"


def test_env_key_overrides_file(tmp_path: Path) -> None:
    store = AppSettingsStore(tmp_path / "s.json", env_api_keys={"DEEPSEEK_API_KEY": "sk-env"})
    store.update(provider="deepseek", api_key="sk-file", token_limit_per_chat=None)
    assert store.api_key_for("deepseek") == "sk-env"


def test_unknown_provider_rejected(store: AppSettingsStore) -> None:
    with pytest.raises(ApiError) as error:
        store.update(provider="nope", api_key=None, token_limit_per_chat=None)
    assert error.value.code == "unknown_provider"


def test_limit_below_minimum_rejected(store: AppSettingsStore) -> None:
    with pytest.raises(ApiError) as error:
        store.update(provider="praxiom", api_key=None, token_limit_per_chat=60_000)
    assert error.value.code == "token_limit_too_low"


def test_switching_provider_rechecks_the_limit(store: AppSettingsStore) -> None:
    store.update(provider="praxiom", api_key=None, token_limit_per_chat=70_000)
    with pytest.raises(ApiError) as error:
        store.update(provider="deepseek", api_key=None, token_limit_per_chat=None)
    assert error.value.code == "token_limit_too_low"


def test_empty_key_rejected(store: AppSettingsStore) -> None:
    with pytest.raises(ApiError) as error:
        store.update(provider="praxiom", api_key="   ", token_limit_per_chat=None)
    assert error.value.code == "empty_api_key"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_file_mode_is_0600(tmp_path: Path, store: AppSettingsStore) -> None:
    store.update(provider="praxiom", api_key="px_live_abc", token_limit_per_chat=None)
    path = tmp_path / "config" / "settings.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_settings_routes(client: TestClient) -> None:
    saved = client.put("/api/settings", headers=AUTH, json={"provider": "praxiom", "api_key": "px_live_xyz12345"})
    assert saved.status_code == 200
    assert saved.json()["keys"]["praxiom"] == {"is_set": True, "prefix": "px_live_"}

    fetched = client.get("/api/settings", headers=AUTH).json()
    assert fetched["provider"] == "praxiom"
    assert {"id": "praxiom", "label": "Praxiom", "min_token_limit": 65_536} in fetched["providers"]


def test_settings_route_error_shape(client: TestClient) -> None:
    response = client.put("/api/settings", headers=AUTH, json={"token_limit_per_chat": 10})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "token_limit_too_low"



CUSTOM = {"base_url": "https://llm.example.com/v1", "model": "my-model", "context_window": 32_000, "max_output_tokens": 4_000}


def test_custom_provider_is_saved_and_resolved(store: AppSettingsStore) -> None:
    view = store.update(provider="custom", api_key="sk-custom", token_limit_per_chat=None, custom=CUSTOM)

    assert view.provider == "custom"
    assert view.custom is not None and view.custom.model == "my-model"
    assert view.keys["custom"] == KeyStatus(is_set=True, prefix="sk-custo")
    spec = store.provider_spec()
    assert (spec.id, spec.base_url, spec.model, spec.max_output_tokens) == ("custom", "https://llm.example.com/v1", "my-model", 4_000)
    assert spec.history_budget == 28_000
    assert store.api_key_for("custom") == "sk-custom"


def test_custom_provider_needs_its_details(store: AppSettingsStore) -> None:
    with pytest.raises(ApiError) as error:
        store.update(provider="custom", api_key=None, token_limit_per_chat=None)
    assert error.value.code == "custom_provider_incomplete"


@pytest.mark.parametrize(
    ("field", "value"), [("base_url", "ftp://llm.example.com"), ("model", "  "), ("context_window", 1_000)]
)
def test_custom_provider_details_are_checked(store: AppSettingsStore, field: str, value: object) -> None:
    with pytest.raises(ApiError) as error:
        store.update(provider="custom", api_key=None, token_limit_per_chat=None, custom={**CUSTOM, field: value})
    assert error.value.code == "invalid_custom_provider"


def test_custom_provider_appears_in_the_choices(store: AppSettingsStore) -> None:
    ids = [option.id for option in store.view().providers]
    assert ids[-1] == "custom"
