"""create image jobs

Revision ID: 20260819_0001
Revises:
Create Date: 2026-08-19
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260819_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "image_jobs",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("filter_profile_id", sa.String(length=80), nullable=False),
        sa.Column("beautify_profile_id", sa.String(length=80), nullable=False),
        sa.Column("enhance_level", sa.Integer(), nullable=False),
        sa.Column("max_selected", sa.Integer(), nullable=False),
        sa.Column("total_count", sa.Integer(), nullable=False),
        sa.Column("processed_count", sa.Integer(), nullable=False),
        sa.Column("selected_count", sa.Integer(), nullable=False),
        sa.Column("rejected_count", sa.Integer(), nullable=False),
        sa.Column("callback_url", sa.String(length=2048), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_image_jobs_status", "image_jobs", ["status"])

    op.create_table(
        "image_items",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("job_id", sa.String(length=40), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("thumbnail_object_key", sa.String(length=1024), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("aspect_ratio", sa.Numeric(10, 4), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("phash", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["image_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_image_items_job_id", "image_items", ["job_id"])
    op.create_index("ix_image_items_phash", "image_items", ["phash"])
    op.create_index("ix_image_items_sha256", "image_items", ["sha256"])
    op.create_index("ix_image_items_status", "image_items", ["status"])


def downgrade() -> None:
    op.drop_index("ix_image_items_status", table_name="image_items")
    op.drop_index("ix_image_items_sha256", table_name="image_items")
    op.drop_index("ix_image_items_phash", table_name="image_items")
    op.drop_index("ix_image_items_job_id", table_name="image_items")
    op.drop_table("image_items")
    op.drop_index("ix_image_jobs_status", table_name="image_jobs")
    op.drop_table("image_jobs")
