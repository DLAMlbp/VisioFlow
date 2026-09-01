from __future__ import annotations

from urllib.error import HTTPError

import pytest
from redis.exceptions import RedisError

from src.core.config import Settings
from src.services.images import vision_rate_limit


class FakeRedis:
    def __init__(self, *, fail_acquire: bool = False) -> None:
        self.fail_acquire = fail_acquire
        self.eval_calls: list[tuple[object, ...]] = []
        self.removed: list[tuple[str, str]] = []
        self.closed = False

    async def eval(self, *args):
        self.eval_calls.append(args)
        if self.fail_acquire:
            raise RedisError("unavailable")
        if args[0] == vision_rate_limit._ACQUIRE_SCRIPT:
            return [1, 0, 1, 1, 0]
        return 1

    async def zrem(self, key: str, token: str) -> None:
        self.removed.append((key, token))

    async def aclose(self) -> None:
        self.closed = True


def _settings(**updates) -> Settings:
    return Settings(
        _env_file=None,
        ai_global_scheduler_enabled=True,
        ai_tagging_api_key="test-key",
        **updates,
    )


@pytest.mark.asyncio
async def test_run_vision_request_acquires_and_releases_global_slot(monkeypatch) -> None:
    redis = FakeRedis()
    monkeypatch.setattr(
        vision_rate_limit.Redis,
        "from_url",
        lambda *_args, **_kwargs: redis,
    )

    result = await vision_rate_limit.run_vision_request(
        _settings(),
        operation="classification",
        request=lambda: {"ok": True},
    )

    assert result == {"ok": True}
    assert redis.eval_calls[0][1] == 4
    assert len(redis.removed) == 1
    assert redis.closed is True


@pytest.mark.asyncio
async def test_http_429_publishes_shared_provider_cooldown(monkeypatch) -> None:
    redis = FakeRedis()
    monkeypatch.setattr(
        vision_rate_limit.Redis,
        "from_url",
        lambda *_args, **_kwargs: redis,
    )
    error = HTTPError(
        "https://example.test/v1/chat/completions",
        429,
        "rate limited",
        {"Retry-After": "3"},
        None,
    )

    with pytest.raises(HTTPError):
        await vision_rate_limit.run_vision_request(
            _settings(),
            operation="beautify_planning",
            request=lambda: (_ for _ in ()).throw(error),
        )

    assert len(redis.eval_calls) == 2
    assert redis.eval_calls[1][2].endswith(":cooldown")
    assert redis.eval_calls[1][3].endswith(":capacity")
    assert len(redis.removed) == 1


@pytest.mark.asyncio
async def test_scheduler_fails_open_when_redis_is_temporarily_unavailable(monkeypatch) -> None:
    redis = FakeRedis(fail_acquire=True)
    monkeypatch.setattr(
        vision_rate_limit.Redis,
        "from_url",
        lambda *_args, **_kwargs: redis,
    )

    result = await vision_rate_limit.run_vision_request(
        _settings(),
        operation="content_analysis",
        request=lambda: "completed",
    )

    assert result == "completed"
    assert redis.removed == []
    assert redis.closed is True


def test_retry_countdown_uses_bounded_exponential_backoff() -> None:
    settings = _settings(
        ai_tagging_retry_base_seconds=10,
        ai_tagging_max_retry_delay_seconds=60,
    )

    assert [vision_rate_limit.retry_countdown(settings, index) for index in range(5)] == [
        10,
        20,
        40,
        60,
        60,
    ]
