"""repair fail-fast job columns after stamped migration drift

Revision ID: 20260904_0043
Revises: 20260903_0042
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260904_0043"
down_revision: str | None = "20260903_0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


REQUIRED_COLUMNS = {
    "deadline_at": sa.DateTime(timezone=True),
    "failed_node": sa.String(64),
    "failure_code": sa.String(64),
    "failure_message": sa.String(1000),
    "failed_image_id": sa.String(40),
    "failure_duration_ms": sa.Integer(),
    "upstream_status_code": sa.Integer(),
    "failed_at": sa.DateTime(timezone=True),
}


def upgrade() -> None:
    """Idempotently repair databases stamped past 0036 without its columns."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    existing = {column["name"] for column in inspector.get_columns("image_jobs")}
    for name, column_type in REQUIRED_COLUMNS.items():
        if name not in existing:
            op.add_column("image_jobs", sa.Column(name, column_type, nullable=True))
    if "deadline_at" not in existing:
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
    # Keep repaired columns to avoid recreating the runtime failure.
    pass
