"""persist fail-fast pipeline diagnostics and job deadlines

Revision ID: 20260903_0036
Revises: 20260903_0035
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260903_0036"
down_revision: str | None = "20260903_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("image_jobs", sa.Column("deadline_at", sa.DateTime(timezone=True)))
    op.add_column("image_jobs", sa.Column("failed_node", sa.String(64)))
    op.add_column("image_jobs", sa.Column("failure_code", sa.String(64)))
    op.add_column("image_jobs", sa.Column("failure_message", sa.String(1000)))
    op.add_column("image_jobs", sa.Column("failed_image_id", sa.String(40)))
    op.add_column("image_jobs", sa.Column("failure_duration_ms", sa.Integer()))
    op.add_column("image_jobs", sa.Column("upstream_status_code", sa.Integer()))
    op.add_column("image_jobs", sa.Column("failed_at", sa.DateTime(timezone=True)))
    op.create_index("ix_image_jobs_deadline_at", "image_jobs", ["deadline_at"])
    op.execute(
        """
        UPDATE image_jobs
        SET deadline_at = created_at + INTERVAL '30 minutes'
        WHERE deadline_at IS NULL
          AND status NOT IN ('completed', 'partial_failed', 'failed', 'cancelled')
        """
    )


def downgrade() -> None:
    op.drop_index("ix_image_jobs_deadline_at", table_name="image_jobs")
    for name in (
        "failed_at",
        "upstream_status_code",
        "failure_duration_ms",
        "failed_image_id",
        "failure_message",
        "failure_code",
        "failed_node",
        "deadline_at",
    ):
        op.drop_column("image_jobs", name)
