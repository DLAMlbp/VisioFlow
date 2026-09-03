"""repair redaction columns after the 0033 revision changed in place

Revision ID: 20260903_0035
Revises: 20260903_0034
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260903_0035"
down_revision: str | None = "20260903_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


REQUIRED_COLUMNS = {
    "redaction_profile_id": sa.String(80),
    "redaction_profile_snapshot": sa.JSON(),
}


def upgrade() -> None:
    """Idempotently repair databases stamped past 0033 without its columns."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    for table in ("image_jobs", "upload_batches"):
        existing = {column["name"] for column in inspector.get_columns(table)}
        for name, column_type in REQUIRED_COLUMNS.items():
            if name not in existing:
                op.add_column(table, sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    # Never drop repaired columns: doing so would recreate the production
    # outage and could destroy redaction selections already stored.
    pass
