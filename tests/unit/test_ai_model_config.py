import json

import pytest
from pydantic import ValidationError

from src.api.model_config import UpdateAIModelConfigRequest
from src.core.config import Settings
from src.services import ai_model_config


class FakeRedis:
    def __init__(self, stored: dict[str, object] | None = None) -> None:
        self.value = json.dumps(stored) if stored is not None else None

    def get(self, _key: str) -> str | None:
        return self.value

    def set(self, _key: str, value: str) -> None:
        self.value = value


def test_runtime_config_cannot_override_locked_model_or_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = FakeRedis(
        {
            "ai_tagging_enabled": False,
            "ai_tagging_base_url": "https://api.example.com/v1",
            "ai_tagging_model": "alternate-model",
        }
    )
    monkeypatch.setattr(ai_model_config, "_redis", lambda _settings: redis)

    loaded = ai_model_config.load_ai_model_settings(Settings())

    assert loaded.ai_tagging_enabled is False
    assert loaded.ai_tagging_base_url == "https://router.keenlight.ai/v1"
    assert loaded.ai_tagging_model == "gpt-5.6-luna"


def test_saved_runtime_config_always_uses_locked_model_and_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = FakeRedis()
    monkeypatch.setattr(ai_model_config, "_redis", lambda _settings: redis)

    saved = ai_model_config.save_ai_model_settings(
        Settings(ai_config_encryption_key="encryption-secret-for-tests-123456"),
        enabled=True,
        api_key="secret-for-test",
    )

    assert saved.ai_tagging_base_url == "https://router.keenlight.ai/v1"
    stored = json.loads(redis.value or "{}")
    assert stored == {
        "ai_tagging_enabled": True,
        "ai_tagging_base_url": "https://router.keenlight.ai/v1",
        "ai_tagging_model": "gpt-5.6-luna",
        "ai_tagging_api_key_encrypted": stored["ai_tagging_api_key_encrypted"],
    }
    assert "secret-for-test" not in (redis.value or "")
    loaded = ai_model_config.load_ai_model_settings(
        Settings(ai_config_encryption_key="encryption-secret-for-tests-123456")
    )
    assert loaded.ai_tagging_api_key == "secret-for-test"


def test_update_request_rejects_model_or_base_url_override() -> None:
    with pytest.raises(ValidationError):
        UpdateAIModelConfigRequest.model_validate(
            {
                "enabled": True,
                "base_url": "https://api.example.com/v1",
                "model": "different-model",
            }
        )


def test_update_request_allows_key_only_configuration() -> None:
    request = UpdateAIModelConfigRequest.model_validate(
        {"enabled": True, "api_key": "secret-for-test"}
    )

    assert request.enabled is True
    assert request.api_key == "secret-for-test"


def test_saved_runtime_config_ignores_legacy_model(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = FakeRedis({"ai_tagging_model": "configured-model"})
    monkeypatch.setattr(ai_model_config, "_redis", lambda _settings: redis)

    saved = ai_model_config.save_ai_model_settings(
        Settings(ai_config_encryption_key="encryption-secret-for-tests-123456"),
        enabled=True,
        api_key="secret-for-test",
    )

    assert saved.ai_tagging_model == "gpt-5.6-luna"
