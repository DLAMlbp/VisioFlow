from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from typing import TypeVar
from urllib.error import HTTPError

from redis.asyncio import Redis
from redis.exceptions import RedisError

from src.core.config import Settings
from src.core.metrics import emit_metric

logger = logging.getLogger(__name__)

T = TypeVar("T")

_ACQUIRE_SCRIPT = """
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local rate_limit = tonumber(ARGV[3])
local concurrency_limit = tonumber(ARGV[4])
local lease_ms = tonumber(ARGV[5])
local token = ARGV[6]

local reduced_limit = tonumber(redis.call('GET', KEYS[4]) or concurrency_limit)
concurrency_limit = math.min(concurrency_limit, reduced_limit)

local cooldown_until = tonumber(redis.call('GET', KEYS[3]) or '0')
if cooldown_until > now_ms then
  return {0, cooldown_until - now_ms, 0, 0, 1}
end

redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, now_ms - window_ms)
redis.call('ZREMRANGEBYSCORE', KEYS[2], 0, now_ms)

local rate_count = redis.call('ZCARD', KEYS[1])
local concurrency_count = redis.call('ZCARD', KEYS[2])
if rate_count < rate_limit and concurrency_count < concurrency_limit then
  redis.call('ZADD', KEYS[1], now_ms, token)
  redis.call('PEXPIRE', KEYS[1], window_ms + 5000)
  redis.call('ZADD', KEYS[2], now_ms + lease_ms, token)
  redis.call('PEXPIRE', KEYS[2], lease_ms + 5000)
  return {1, 0, rate_count + 1, concurrency_count + 1, 0}
end

local wait_ms = 250
if rate_count >= rate_limit then
  local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  if oldest[2] then
    wait_ms = math.max(wait_ms, tonumber(oldest[2]) + window_ms - now_ms)
  end
end
if concurrency_count >= concurrency_limit then
  local earliest = redis.call('ZRANGE', KEYS[2], 0, 0, 'WITHSCORES')
  if earliest[2] then
    wait_ms = math.max(wait_ms, tonumber(earliest[2]) - now_ms)
  end
end
return {0, wait_ms, rate_count, concurrency_count, 0}
"""


async def run_vision_request(
    settings: Settings,
    *,
    operation: str,
    request: Callable[[], T],
) -> T:
    """Run one provider request under the shared cross-worker capacity guard."""
    token: str | None = None
    redis: Redis | None = None
    wait_started = time.perf_counter()
    if settings.ai_global_scheduler_enabled:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            token = await _acquire(redis, settings)
        except RedisError:
            logger.exception("AI scheduler unavailable; request is proceeding without global guard")
            emit_metric(logger, "vision_scheduler_fail_open_total", labels={"operation": operation})
            await redis.aclose()
            redis = None

    emit_metric(
        logger,
        "vision_scheduler_wait_ms",
        value=round((time.perf_counter() - wait_started) * 1000),
        labels={"operation": operation},
    )
    request_started = time.perf_counter()
    try:
        result = await asyncio.to_thread(request)
    except Exception as exc:
        duration_ms = round((time.perf_counter() - request_started) * 1000)
        status_code = exc.code if isinstance(exc, HTTPError) else None
        emit_metric(
            logger,
            "vision_request_duration_ms",
            value=duration_ms,
            labels={
                "operation": operation,
                "outcome": "error",
                "status_code": status_code,
                "error_type": type(exc).__name__,
            },
        )
        if redis is not None and isinstance(exc, HTTPError) and exc.code == 429:
            await _set_cooldown(redis, settings, exc)
        raise
    else:
        emit_metric(
            logger,
            "vision_request_duration_ms",
            value=round((time.perf_counter() - request_started) * 1000),
            labels={"operation": operation, "outcome": "success"},
        )
        return result
    finally:
        if redis is not None:
            if token is not None:
                try:
                    await redis.zrem(_inflight_key(settings), token)
                except RedisError:
                    logger.warning("Unable to release AI scheduler lease token=%s", token)
            await redis.aclose()


async def acquire_vision_rate_slot(settings: Settings) -> None:
    """Backward-compatible RPM-only acquisition for non-HTTP legacy callers."""
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    token: str | None = None
    try:
        token = await _acquire(redis, settings)
    finally:
        if token is not None:
            await redis.zrem(_inflight_key(settings), token)
        await redis.aclose()


async def _acquire(redis: Redis, settings: Settings) -> str:
    token = uuid.uuid4().hex
    lease_ms = (settings.ai_tagging_timeout_seconds + 30) * 1000
    while True:
        now_ms = int(time.time() * 1000)
        result = await redis.eval(
            _ACQUIRE_SCRIPT,
            4,
            _rate_key(settings),
            _inflight_key(settings),
            _cooldown_key(settings),
            _capacity_key(settings),
            now_ms,
            60_000,
            max(1, settings.ai_tagging_rate_limit_per_minute),
            max(1, settings.ai_tagging_global_concurrency),
            lease_ms,
            token,
        )
        if int(result[0]) == 1:
            return token
        wait_ms = max(100, min(1000, int(result[1])))
        await asyncio.sleep(wait_ms / 1000)


async def _set_cooldown(redis: Redis, settings: Settings, error: HTTPError) -> None:
    retry_after = _retry_after_seconds(error)
    delay = retry_after or settings.ai_tagging_retry_base_seconds
    delay = max(1, min(delay, settings.ai_tagging_max_retry_delay_seconds))
    cooldown_until = int((time.time() + delay) * 1000)
    reduced_limit = max(1, settings.ai_tagging_global_concurrency // 2)
    script = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local proposed = tonumber(ARGV[1])
if proposed > current then
  redis.call('SET', KEYS[1], proposed, 'PX', ARGV[2])
end
redis.call('SET', KEYS[2], ARGV[3], 'PX', ARGV[4])
return math.max(current, proposed)
"""
    try:
        await redis.eval(
            script,
            2,
            _cooldown_key(settings),
            _capacity_key(settings),
            cooldown_until,
            (delay + 5) * 1000,
            reduced_limit,
            settings.ai_tagging_capacity_recovery_seconds * 1000,
        )
    except RedisError:
        logger.warning("Unable to publish AI provider cooldown")
    emit_metric(
        logger,
        "vision_provider_cooldown_seconds",
        value=delay,
        labels={"status_code": 429, "reduced_concurrency": reduced_limit},
    )


def retry_countdown(settings: Settings, retry_index: int) -> int:
    return min(
        settings.ai_tagging_max_retry_delay_seconds,
        settings.ai_tagging_retry_base_seconds * (2 ** max(0, retry_index)),
    )


def _retry_after_seconds(error: HTTPError) -> int | None:
    value = error.headers.get("Retry-After") if error.headers is not None else None
    if value is None:
        return None
    try:
        return max(1, int(float(value)))
    except (TypeError, ValueError):
        return None


def _scheduler_namespace(settings: Settings) -> str:
    return f"image-ai:vision-scheduler:{settings.ai_tagging_provider}"


def _rate_key(settings: Settings) -> str:
    return f"{_scheduler_namespace(settings)}:rate"


def _inflight_key(settings: Settings) -> str:
    return f"{_scheduler_namespace(settings)}:inflight"


def _cooldown_key(settings: Settings) -> str:
    return f"{_scheduler_namespace(settings)}:cooldown"


def _capacity_key(settings: Settings) -> str:
    return f"{_scheduler_namespace(settings)}:capacity"
