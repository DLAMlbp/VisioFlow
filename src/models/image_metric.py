from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

if TYPE_CHECKING:
    from src.models.image_item import ImageItem


class ImageMetric(Base):
    __tablename__ = "image_metrics"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    image_id: Mapped[str] = mapped_column(
        ForeignKey("image_items.id", ondelete="CASCADE"), unique=True
    )

    sharpness_score: Mapped[float] = mapped_column(Float, nullable=False)
    exposure_score: Mapped[float] = mapped_column(Float, nullable=False)
    contrast_score: Mapped[float] = mapped_column(Float, nullable=False)
    noise_score: Mapped[float] = mapped_column(Float, nullable=False)

    raw_metrics_json: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    image: Mapped[ImageItem] = relationship(back_populates="metric")
