from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base
from src.models.image_ai_tag import ImageAITag

if TYPE_CHECKING:
    from src.models.image_job import ImageJob
    from src.models.image_metric import ImageMetric
    from src.models.image_result import ImageResult


class ImageItem(Base):
    __tablename__ = "image_items"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("image_jobs.id", ondelete="CASCADE"), index=True)

    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    thumbnail_object_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    aspect_ratio: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    exif_orientation: Mapped[int | None] = mapped_column(Integer, nullable=True)

    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    phash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    reject_codes: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    job: Mapped[ImageJob] = relationship(back_populates="items")
    metric: Mapped[ImageMetric | None] = relationship(back_populates="image", lazy="selectin")
    result: Mapped[ImageResult | None] = relationship(back_populates="image", lazy="selectin")
    ai_tag: Mapped[ImageAITag | None] = relationship(back_populates="image", lazy="selectin")
