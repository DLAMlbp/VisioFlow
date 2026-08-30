from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

if TYPE_CHECKING:
    from src.models.image_job import ImageJob


class UploadBatch(Base):
    __tablename__ = "upload_batches"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    filter_profile_id: Mapped[str] = mapped_column(String(80), nullable=False)
    beautify_profile_id: Mapped[str] = mapped_column(String(80), nullable=False)
    filter_profile_snapshot: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    beautify_profile_snapshot: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    processing_standard_snapshots: Mapped[list[dict[str, object]] | None] = mapped_column(
        JSON, nullable=True
    )
    filter_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    beautify_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    similarity_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    similarity_profile_id: Mapped[str] = mapped_column(String(80), nullable=False)
    unmatched_standard_policy: Mapped[str] = mapped_column(
        String(16), nullable=False, default="reject"
    )
    library_scope_node_id: Mapped[str | None] = mapped_column(
        ForeignKey("library_tag_nodes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    enhance_level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_selected: Mapped[int] = mapped_column(Integer, nullable=False)
    callback_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("image_jobs.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list[UploadBatchItem]] = relationship(
        back_populates="batch", cascade="all, delete-orphan", lazy="selectin"
    )
    job: Mapped[ImageJob | None] = relationship()


class UploadBatchItem(Base):
    __tablename__ = "upload_batch_items"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("upload_batches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="registered", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    batch: Mapped[UploadBatch] = relationship(back_populates="items")
