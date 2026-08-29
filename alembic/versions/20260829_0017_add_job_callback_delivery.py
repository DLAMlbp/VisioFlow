"""add durable image job callback delivery state

Revision ID: 20260829_0017
Revises: 20260828_0016
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260829_0017"
down_revision: str | None = "20260828_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("image_jobs", sa.Column("callback_status", sa.String(32), nullable=True))
    op.add_column(
        "image_jobs",
        sa.Column("callback_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "image_jobs", sa.Column("callback_next_attempt_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "image_jobs", sa.Column("callback_last_attempt_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "image_jobs", sa.Column("callback_delivered_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("image_jobs", sa.Column("callback_last_error", sa.String(1000), nullable=True))
    op.create_index("ix_image_jobs_callback_status", "image_jobs", ["callback_status"])


def downgrade() -> None:
    op.drop_index("ix_image_jobs_callback_status", table_name="image_jobs")
    op.drop_column("image_jobs", "callback_last_error")
    op.drop_column("image_jobs", "callback_delivered_at")
    op.drop_column("image_jobs", "callback_last_attempt_at")
    op.drop_column("image_jobs", "callback_next_attempt_at")
    op.drop_column("image_jobs", "callback_attempts")
    op.drop_column("image_jobs", "callback_status")
