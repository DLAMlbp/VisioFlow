from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base
from src.models.image_ai_tag import ImageAITag

if TYPE_CHECKING:
    from src.models.image_job import ImageJob
    from src.models.image_metric import ImageMetric
    from src.models.image_result import ImageResult
    from src.models.image_similarity_match import ImageSimilarityMatch


class ImageItem(Base):
    __tablename__ = "image_items"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("image_jobs.id", ondelete="CASCADE"), index=True)

    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    thumbnail_object_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    analysis_object_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_object_key: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    aspect_ratio: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    exif_orientation: Mapped[int | None] = mapped_column(Integer, nullable=True)

    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    phash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(512), nullable=True)
    embedding_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reject_codes: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    ai_processing_status: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    ai_processing_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    ai_processing_diagnostic_json: Mapped[dict[str, object] | None] = mapped_column(
        JSON, nullable=True
    )
    ai_processing_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ai_processing_prompt_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ai_processing_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_processing_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ai_processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ai_processing_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    completion_status: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    completion_label: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    completion_subtype: Mapped[str | None] = mapped_column(String(32), nullable=True)
    completion_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    completion_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    completion_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    completion_prompt_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    completion_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    completion_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completion_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    routed_filter_profile_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    routed_filter_profile_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    review_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )

    beautify_plan_status: Mapped[str | None] = mapped_column(
        String(24), nullable=True, index=True
    )
    beautify_plan_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    beautify_plan_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    beautify_plan_prompt_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    beautify_plan_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    beautify_plan_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    beautify_plan_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    beautify_plan_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    analysis_status: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    embedding_status: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    match_status: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)

    preprocess_dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    preprocess_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    preprocess_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enhance_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enhance_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enhancement_stage: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    enhancement_stage_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    enhancement_recovery_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    analysis_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    analysis_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    embedding_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    embedding_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    match_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    match_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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
    similarity_match: Mapped[ImageSimilarityMatch | None] = relationship(
        back_populates="image", cascade="all, delete-orphan", uselist=False, lazy="selectin"
    )
