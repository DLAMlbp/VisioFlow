from __future__ import annotations

import json

from redis import Redis
from redis.exceptions import RedisError

from src.core.config import Settings

_CONFIG_KEY = "image_intelligence:ai_model_config"
_CONFIG_FIELDS = ("ai_tagging_enabled", "ai_tagging_base_url", "ai_tagging_model", "ai_tagging_api_key")


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
    return settings.model_copy(update=update) if update else settings


def save_ai_model_settings(
    settings: Settings,
    *,
    enabled: bool,
    base_url: str,
    model: str,
    api_key: str | None,
) -> Settings:
    current = load_ai_model_settings(settings)
    payload = {
        "ai_tagging_enabled": enabled,
        "ai_tagging_base_url": base_url.rstrip("/"),
        "ai_tagging_model": model,
        "ai_tagging_api_key": api_key if api_key is not None else current.ai_tagging_api_key,
    }
    _redis(settings).set(_CONFIG_KEY, json.dumps(payload))
    return settings.model_copy(update=payload)


def _redis(settings: Settings) -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)
