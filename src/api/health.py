import asyncio

from fastapi import APIRouter, Response, status
from redis.asyncio import Redis
from sqlalchemy import text

from src.core.config import get_settings
from src.db.session import AsyncSessionLocal
from src.services.storage.factory import get_storage_provider

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness(response: Response) -> dict[str, object]:
    settings = get_settings()

    async def check_database() -> None:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))

    async def check_redis() -> None:
        client = Redis.from_url(settings.redis_url)
        try:
            await client.ping()
        finally:
            await client.aclose()

    checks = await asyncio.gather(
        check_database(),
        check_redis(),
        get_storage_provider().healthcheck(),
        return_exceptions=True,
    )
    names = ("database", "redis", "object_storage")
    services = {
        name: "ok" if not isinstance(result, Exception) else "unavailable"
        for name, result in zip(names, checks, strict=True)
    }
    ready = all(value == "ok" for value in services.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if ready else "not_ready", "services": services}
