from __future__ import annotations

import math
import time
from dataclasses import dataclass

from redis import Redis
from redis.exceptions import RedisError

from src.core.config import Settings

_PIPELINE_QUEUES = (
    "preprocess",
    "classification",
    "filtering",
    "beautify_plan",
    "redaction",
    "inpaint",
    "enhance",
    "render",
    "analysis",
    "openclip",
    "matching",
)

# Kombu's Redis transport stores priority 0 in the logical queue and higher
# priorities in lists named ``<queue>\x06\x16<priority>``.  Older project
# releases used ``<queue>:<priority>`` in operational scripts, so include both
# physical layouts while those keys can still exist in persistent Redis.
_REDIS_PRIORITY_SEPARATOR = "\x06\x16"
_LEGACY_PRIORITY_SEPARATOR = ":"

_TOKEN_BUCKET_SCRIPT = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local refill_per_minute = tonumber(ARGV[2])
local capacity = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])
local ttl_ms = tonumber(ARGV[5])

local state = redis.call('HMGET', key, 'tokens', 'updated_at_ms')
local tokens = tonumber(state[1]) or capacity
local updated_at_ms = tonumber(state[2]) or now_ms
local elapsed_ms = math.max(0, now_ms - updated_at_ms)
tokens = math.min(capacity, tokens + elapsed_ms * refill_per_minute / 60000)

if tokens < cost then
  local retry_after_ms = math.ceil((cost - tokens) * 60000 / refill_per_minute)
  redis.call('HSET', key, 'tokens', tokens, 'updated_at_ms', now_ms)
  redis.call('PEXPIRE', key, ttl_ms)
  return {0, retry_after_ms, math.floor(tokens)}
end

tokens = tokens - cost
redis.call('HSET', key, 'tokens', tokens, 'updated_at_ms', now_ms)
redis.call('PEXPIRE', key, ttl_ms)
return {1, 0, math.floor(tokens)}
"""


@dataclass(frozen=True)
class IntegrationAdmissionSnapshot:
    queue_depth: int
    remaining_burst_images: int


class IntegrationAdmissionRejected(RuntimeError):
    def __init__(self, message: str, *, retry_after_seconds: int, queue_depth: int) -> None:
        super().__init__(message)
        self.retry_after_seconds = max(1, retry_after_seconds)
        self.queue_depth = max(0, queue_depth)


class IntegrationAdmissionUnavailable(RuntimeError):
    pass


def enforce_integration_admission(
    settings: Settings,
    image_count: int,
    *,
    client: Redis | None = None,
) -> IntegrationAdmissionSnapshot:
    if not settings.integration_admission_enabled:
        return IntegrationAdmissionSnapshot(queue_depth=0, remaining_burst_images=0)
    if image_count <= 0:
        return IntegrationAdmissionSnapshot(queue_depth=0, remaining_burst_images=0)
    if image_count > settings.integration_rate_limit_burst_images:
        raise IntegrationAdmissionRejected(
            "单次图片数量超过服务当前允许的突发容量",
            retry_after_seconds=60,
            queue_depth=0,
        )

    redis_client = client or Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    try:
        queue_depth = _pipeline_queue_depth(redis_client)
        if queue_depth + image_count > settings.integration_max_pipeline_queue_depth:
            raise IntegrationAdmissionRejected(
                "图片处理队列繁忙，请稍后重试",
                retry_after_seconds=300,
                queue_depth=queue_depth,
            )

        now_ms = int(time.time() * 1000)
        refill = settings.integration_rate_limit_images_per_minute
        capacity = settings.integration_rate_limit_burst_images
        ttl_ms = max(120000, math.ceil(capacity / refill * 120000))
        allowed, retry_after_ms, remaining = redis_client.eval(
            _TOKEN_BUCKET_SCRIPT,
            1,
            "image-intelligence:admission:integration",
            now_ms,
            refill,
            capacity,
            image_count,
            ttl_ms,
        )
    except IntegrationAdmissionRejected:
        raise
    except RedisError as exc:
        raise IntegrationAdmissionUnavailable("无法读取任务队列容量") from exc

    if not int(allowed):
        raise IntegrationAdmissionRejected(
            "图片提交频率超过服务处理能力，请按 Retry-After 稍后重试",
            retry_after_seconds=math.ceil(int(retry_after_ms) / 1000),
            queue_depth=queue_depth,
        )
    return IntegrationAdmissionSnapshot(
        queue_depth=queue_depth,
        remaining_burst_images=max(0, int(remaining)),
    )


def _pipeline_queue_depth(client: Redis) -> int:
    pipeline = client.pipeline(transaction=False)
    for queue in _PIPELINE_QUEUES:
        pipeline.llen(queue)
        for priority in range(1, 10):
            pipeline.llen(f"{queue}{_REDIS_PRIORITY_SEPARATOR}{priority}")
            pipeline.llen(f"{queue}{_LEGACY_PRIORITY_SEPARATOR}{priority}")
    return sum(int(value or 0) for value in pipeline.execute())
