from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

if TYPE_CHECKING:
    from src.models.image_item import ImageItem


class ImageJob(Base):
    __tablename__ = "image_jobs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    filter_profile_id: Mapped[str] = mapped_column(String(80), nullable=False)
    beautify_profile_id: Mapped[str] = mapped_column(String(80), nullable=False)
    redaction_profile_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    filter_profile_snapshot: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    beautify_profile_snapshot: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    redaction_profile_snapshot: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    processing_standard_snapshots: Mapped[list[dict[str, object]] | None] = mapped_column(
        JSON, nullable=True
    )
    routing_mode: Mapped[str] = mapped_column(String(24), nullable=False, default="legacy")
    completion_profile_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    completion_profile_snapshot: Mapped[dict[str, object] | None] = mapped_column(
        JSON, nullable=True
    )
    completed_filter_profile_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    completed_filter_profile_snapshot: Mapped[dict[str, object] | None] = mapped_column(
        JSON, nullable=True
    )
    non_completed_filter_profile_id: Mapped[str | None] = mapped_column(
        String(80), nullable=True
    )
    non_completed_filter_profile_snapshot: Mapped[dict[str, object] | None] = mapped_column(
        JSON, nullable=True
    )
    routing_policy_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    filter_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    beautify_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    watermark_processing_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    similarity_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    similarity_profile_id: Mapped[str] = mapped_column(
        String(80), nullable=False, default="library_similarity_v2"
    )
    unmatched_standard_policy: Mapped[str] = mapped_column(
        String(16), nullable=False, default="reject"
    )
    ai_tagging_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    enhance_level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_selected: Mapped[int] = mapped_column(Integer, nullable=False)

    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    selected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    not_selected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dispatch_cursor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    callback_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    callback_contract: Mapped[str] = mapped_column(
        String(32), nullable=False, default="native_v1", server_default="native_v1"
    )
    callback_status: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    callback_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    callback_next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    callback_last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    callback_delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    callback_last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    failed_node: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    failed_image_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    failure_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    upstream_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    items: Mapped[list[ImageItem]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        lazy="raise",
    )
