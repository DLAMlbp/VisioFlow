from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.upload_batch import UploadBatch


class UploadBatchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, batch: UploadBatch) -> UploadBatch:
        self.session.add(batch)
        await self.session.commit()
        await self.session.refresh(batch)
        return batch

    async def get_for_update(self, batch_id: str) -> UploadBatch | None:
        result = await self.session.execute(
            select(UploadBatch)
            .where(UploadBatch.id == batch_id)
            .options(selectinload(UploadBatch.items))
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def complete(self, batch: UploadBatch, *, job_id: str, completed_at: datetime) -> None:
        batch.status = "completed"
        batch.job_id = job_id
        batch.completed_at = completed_at
        await self.session.commit()
