"""add image aspect normalization audit

Revision ID: 20260910_0048
Revises: 20260910_0047
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260910_0048"
down_revision: str | None = "20260910_0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "image_items",
        sa.Column("normalization_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("image_items", "normalization_json")
