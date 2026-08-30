"""add conditional processing standard snapshots

Revision ID: 20260830_0018
Revises: 20260829_0017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260830_0018"
down_revision: str | None = "20260829_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "image_jobs",
        sa.Column("processing_standard_snapshots", sa.JSON(), nullable=True),
    )
    op.add_column(
        "upload_batches",
        sa.Column("processing_standard_snapshots", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("upload_batches", "processing_standard_snapshots")
    op.drop_column("image_jobs", "processing_standard_snapshots")
