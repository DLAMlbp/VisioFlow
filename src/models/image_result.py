from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

if TYPE_CHECKING:
    from src.models.image_item import ImageItem


class ImageResult(Base):
    __tablename__ = "image_results"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    image_id: Mapped[str] = mapped_column(
        ForeignKey("image_items.id", ondelete="CASCADE"), unique=True
    )

    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    final_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    enhanced_object_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    enhanced_metrics_json: Mapped[dict[str, float] | None] = mapped_column(JSON, nullable=True)
    reject_codes_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    reasons_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    image: Mapped[ImageItem] = relationship(back_populates="result")
