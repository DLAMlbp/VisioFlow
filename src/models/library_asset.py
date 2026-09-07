from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

if TYPE_CHECKING:
    from src.models.image_similarity_match import ImageSimilarityMatch
    from src.models.library_asset_group import LibraryAssetGroup


class LibraryAsset(Base):
    __tablename__ = "library_assets"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    original_object_key: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    thumbnail_object_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    group_id: Mapped[str] = mapped_column(
        ForeignKey("library_asset_groups.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    phash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    analysis_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(512), nullable=True)
    embedding_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_group_prototype: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", index=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    group: Mapped[LibraryAssetGroup] = relationship(back_populates="assets")
    matches: Mapped[list[ImageSimilarityMatch]] = relationship(back_populates="matched_asset")
