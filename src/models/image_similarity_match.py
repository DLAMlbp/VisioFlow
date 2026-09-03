from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

if TYPE_CHECKING:
    from src.models.image_item import ImageItem
    from src.models.library_asset import LibraryAsset


class ImageSimilarityMatch(Base):
    __tablename__ = "image_similarity_matches"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    image_id: Mapped[str] = mapped_column(
        ForeignKey("image_items.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    matched_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("library_assets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    matched_tags_snapshot: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    similarity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    feature_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_version: Mapped[str] = mapped_column(
        String(40), nullable=False, default="legacy_v2"
    )
    feature_reliability: Mapped[float | None] = mapped_column(Float, nullable=True)
    feature_coverage: Mapped[float | None] = mapped_column(Float, nullable=True)
    candidate_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    field_scores: Mapped[dict[str, dict[str, object]]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    decision: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    message: Mapped[str] = mapped_column(String(200), nullable=False)
    candidate_json: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    image: Mapped[ImageItem] = relationship(back_populates="similarity_match")
    matched_asset: Mapped[LibraryAsset | None] = relationship(back_populates="matches")
