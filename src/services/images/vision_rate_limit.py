import asyncio
import time

from redis.asyncio import Redis

from src.core.config import Settings


async def acquire_vision_rate_slot(settings: Settings) -> None:
    limit = max(1, settings.ai_tagging_rate_limit_per_minute)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        while True:
            window = int(time.time() // 60)
            key = f"image-ai:vision-rate:{window}"
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, 65)
            if count <= limit:
                return
            ttl = await redis.ttl(key)
            await asyncio.sleep(max(1, min(10, ttl if ttl > 0 else 1)))
    finally:
        await redis.aclose()
