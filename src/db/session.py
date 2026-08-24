from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.asyncio.session import AsyncSession
from sqlalchemy.pool import NullPool

from src.core.config import get_settings

settings = get_settings()
# Celery tasks create a short-lived event loop via asyncio.run(). Avoid reusing
# asyncpg connections that belong to a previous task's event loop.
engine = create_async_engine(settings.database_url, pool_pre_ping=True, poolclass=NullPool)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
