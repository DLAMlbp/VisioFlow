"""scale image pipeline and add upload batches

Revision ID: 20260826_0013
Revises: 20260826_0012
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision: str = "20260826_0013"
down_revision: str | None = "20260826_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("image_jobs", sa.Column("not_selected_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("image_jobs", sa.Column("dispatch_cursor", sa.Integer(), server_default="0", nullable=False))
    op.add_column("image_jobs", sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("image_items", sa.Column("analysis_object_key", sa.String(1024), nullable=True))
    op.add_column("image_items", sa.Column("embedding", Vector(512), nullable=True))
    op.add_column("image_items", sa.Column("embedding_version", sa.String(120), nullable=True))
    for name in ("analysis_status", "embedding_status", "match_status"):
        op.add_column("image_items", sa.Column(name, sa.String(24), nullable=True))
        op.create_index(f"ix_image_items_{name}", "image_items", [name])
    for name in (
        "preprocess_dispatched_at", "preprocess_started_at", "preprocess_completed_at",
        "enhance_started_at", "enhance_completed_at", "analysis_started_at",
        "analysis_completed_at", "embedding_started_at", "embedding_completed_at",
        "match_started_at", "match_completed_at", "purged_at",
    ):
        op.add_column("image_items", sa.Column(name, sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "upload_batches",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("filter_profile_id", sa.String(80), nullable=False),
        sa.Column("beautify_profile_id", sa.String(80), nullable=False),
        sa.Column("similarity_profile_id", sa.String(80), nullable=False),
        sa.Column("enhance_level", sa.Integer(), nullable=False),
        sa.Column("max_selected", sa.Integer(), nullable=False),
        sa.Column("callback_url", sa.String(2048), nullable=True),
        sa.Column("job_id", sa.String(40), sa.ForeignKey("image_jobs.id", ondelete="SET NULL"), unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_upload_batches_status", "upload_batches", ["status"])
    op.create_index("ix_upload_batches_expires_at", "upload_batches", ["expires_at"])
    op.create_table(
        "upload_batch_items",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("batch_id", sa.String(40), sa.ForeignKey("upload_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("object_key", sa.String(1024), nullable=False, unique=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_upload_batch_items_batch_id", "upload_batch_items", ["batch_id"])
    op.create_index("ix_upload_batch_items_status", "upload_batch_items", ["status"])


def downgrade() -> None:
    op.drop_table("upload_batch_items")
    op.drop_table("upload_batches")
    for name in ("analysis_status", "embedding_status", "match_status"):
        op.drop_index(f"ix_image_items_{name}", table_name="image_items")
    for name in (
        "purged_at", "match_completed_at", "match_started_at", "embedding_completed_at",
        "embedding_started_at", "analysis_completed_at", "analysis_started_at",
        "enhance_completed_at", "enhance_started_at", "preprocess_completed_at",
        "preprocess_started_at", "preprocess_dispatched_at", "match_status",
        "embedding_status", "analysis_status", "embedding_version", "embedding",
        "analysis_object_key",
    ):
        op.drop_column("image_items", name)
    op.drop_column("image_jobs", "cancel_requested_at")
    op.drop_column("image_jobs", "dispatch_cursor")
    op.drop_column("image_jobs", "not_selected_count")
