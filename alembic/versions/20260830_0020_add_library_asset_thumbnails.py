"""add durable library asset thumbnails

Revision ID: 20260830_0020
Revises: 20260830_0019
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260830_0020"
down_revision: str | None = "20260830_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "library_assets",
        sa.Column("thumbnail_object_key", sa.String(length=1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("library_assets", "thumbnail_object_key")
