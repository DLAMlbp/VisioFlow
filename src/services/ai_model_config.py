from __future__ import annotations

import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken
from redis import Redis
from redis.exceptions import RedisError

from src.core.config import Settings

_CONFIG_KEY = "image_intelligence:ai_model_config"
_CONFIG_FIELDS = ("ai_tagging_enabled", "ai_tagging_model")


def load_ai_model_settings(settings: Settings) -> Settings:
    """Overlay the optional runtime model configuration on environment defaults."""
    try:
        raw = _redis(settings).get(_CONFIG_KEY)
        if raw is None:
            return settings
        stored = json.loads(raw)
    except (RedisError, json.JSONDecodeError):
        # Processing remains available when Redis is temporarily unavailable.
        return settings

    update = {key: stored[key] for key in _CONFIG_FIELDS if key in stored}
    encrypted_key = stored.get("ai_tagging_api_key_encrypted")
    if isinstance(encrypted_key, str) and encrypted_key:
        try:
            update["ai_tagging_api_key"] = _fernet(settings).decrypt(
                encrypted_key.encode("ascii")
            ).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, ValueError):
            # Keep the environment-backed key when persisted configuration is corrupt
            # or was encrypted with a retired secret.
            pass
    elif settings.app_env != "production":
        # Development-only compatibility for configuration saved before encryption.
        legacy_key = stored.get("ai_tagging_api_key")
        if isinstance(legacy_key, str) and legacy_key:
            update["ai_tagging_api_key"] = legacy_key
    return settings.model_copy(update=update) if update else settings


def save_ai_model_settings(
    settings: Settings,
    *,
    enabled: bool,
    model: str | None,
    api_key: str | None,
) -> Settings:
    current = load_ai_model_settings(settings)
    payload = {
        "ai_tagging_enabled": enabled,
        "ai_tagging_base_url": settings.ai_tagging_base_url,
        "ai_tagging_model": model if model is not None else current.ai_tagging_model,
    }
    effective_api_key = api_key if api_key is not None else current.ai_tagging_api_key
    if effective_api_key:
        payload["ai_tagging_api_key_encrypted"] = _fernet(settings).encrypt(
            effective_api_key.encode("utf-8")
        ).decode("ascii")
    _redis(settings).set(_CONFIG_KEY, json.dumps(payload))
    return settings.model_copy(
        update={
            "ai_tagging_enabled": enabled,
            "ai_tagging_model": payload["ai_tagging_model"],
            "ai_tagging_api_key": effective_api_key,
        }
    )


def _redis(settings: Settings) -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)


def _fernet(settings: Settings) -> Fernet:
    secret = settings.ai_config_encryption_key or (
        settings.api_key if settings.app_env != "production" else ""
    )
    if len(secret) < 32:
        raise ValueError("服务端未配置 AI_CONFIG_ENCRYPTION_KEY")
    derived = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(derived))
