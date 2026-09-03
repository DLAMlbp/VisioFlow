"""enable watermark processing by default

Revision ID: 20260903_0042
Revises: 20260903_0041
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260903_0042"
down_revision: str | None = "20260903_0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table_name in ("image_jobs", "upload_batches"):
        op.alter_column(
            table_name,
            "watermark_processing_enabled",
            existing_type=sa.Boolean(),
            server_default=sa.true(),
            existing_nullable=False,
        )


def downgrade() -> None:
    for table_name in ("image_jobs", "upload_batches"):
        op.alter_column(
            table_name,
            "watermark_processing_enabled",
            existing_type=sa.Boolean(),
            server_default=sa.false(),
            existing_nullable=False,
        )
