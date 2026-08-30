import pytest
from pydantic import ValidationError

from src.core.config import Settings


def _production_settings(**updates) -> Settings:
    values = {
        "app_env": "production",
        "api_key": "a" * 32,
        "integration_api_key": "b" * 32,
        "callback_signing_secret": "d" * 32,
        "callback_allowed_hosts": "client.example.com",
        "ai_tagging_api_key": "model-key",
        "ai_config_encryption_key": "c" * 32,
        "s3_access_key": "production-access",
        "s3_secret_key": "production-secret",
        "trusted_hosts": "images.example.com",
    }
    values.update(updates)
    return Settings(**values)


def test_production_configuration_accepts_explicit_secrets() -> None:
    settings = _production_settings()

    assert settings.app_env == "production"


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"api_key": "short"}, "API_KEY"),
        ({"integration_api_key": ""}, "INTEGRATION_API_KEY"),
        ({"callback_signing_secret": "short"}, "CALLBACK_SIGNING_SECRET"),
        ({"callback_allowed_hosts": "*"}, "CALLBACK_ALLOWED_HOSTS"),
        ({"ai_tagging_api_key": ""}, "AI_TAGGING_API_KEY"),
        ({"ai_config_encryption_key": "short"}, "AI_CONFIG_ENCRYPTION_KEY"),
        ({"s3_access_key": "minio", "s3_secret_key": "minio123"}, "默认对象存储凭据"),
        ({"trusted_hosts": "*"}, "TRUSTED_HOSTS"),
    ],
)
def test_production_configuration_rejects_unsafe_values(updates, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _production_settings(**updates)
