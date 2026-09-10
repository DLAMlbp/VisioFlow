"""add bounded-resolution processing image source

Revision ID: 20260910_0047
Revises: 20260910_0046
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260910_0047"
down_revision: str | None = "20260910_0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "image_items",
        sa.Column("processing_object_key", sa.String(length=1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("image_items", "processing_object_key")
