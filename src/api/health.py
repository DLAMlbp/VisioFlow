import asyncio

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Response, status
from redis.asyncio import Redis
from sqlalchemy import text

from src.core.config import get_settings
from src.db.session import AsyncSessionLocal
from src.services.ai_model_config import load_ai_model_settings
from src.services.jobs.workflow_config import required_workflow_error
from src.services.storage.factory import get_storage_provider

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness(response: Response) -> dict[str, object]:
    settings = get_settings()

    async def check_database_schema() -> None:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
            rows = await session.execute(text("SELECT version_num FROM alembic_version"))
            database_heads = set(rows.scalars())
        code_heads = set(ScriptDirectory.from_config(Config("alembic.ini")).get_heads())
        if database_heads != code_heads:
            raise RuntimeError("数据库迁移版本与应用代码不一致")

    async def check_redis() -> None:
        client = Redis.from_url(settings.redis_url)
        try:
            await client.ping()
        finally:
            await client.aclose()

    async def check_configuration() -> None:
        workflow_error = required_workflow_error(settings)
        if workflow_error:
            raise RuntimeError(workflow_error)
        if not settings.api_key:
            raise RuntimeError("API_KEY 未配置")
        effective = await asyncio.to_thread(load_ai_model_settings, settings)
        if effective.ai_tagging_enabled and not effective.ai_tagging_api_key:
            raise RuntimeError("AI_TAGGING_API_KEY 未配置")

    checks = await asyncio.gather(
        check_database_schema(),
        check_redis(),
        get_storage_provider().healthcheck(),
        check_configuration(),
        return_exceptions=True,
    )
    names = ("database_schema", "redis", "object_storage", "configuration")
    services = {
        name: "ok" if not isinstance(result, Exception) else "unavailable"
        for name, result in zip(names, checks, strict=True)
    }
    ready = all(value == "ok" for value in services.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if ready else "not_ready", "services": services}
