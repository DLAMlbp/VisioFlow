"""add durable enhancement substage cursor

Revision ID: 20260903_0039
Revises: 20260903_0038
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260903_0039"
down_revision: str | None = "20260903_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "image_items",
        sa.Column("enhancement_stage", sa.String(length=24), nullable=True),
    )
    op.add_column(
        "image_items",
        sa.Column("enhancement_stage_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_image_items_enhancement_stage",
        "image_items",
        ["enhancement_stage"],
    )


def downgrade() -> None:
    op.drop_index("ix_image_items_enhancement_stage", table_name="image_items")
    op.drop_column("image_items", "enhancement_stage_started_at")
    op.drop_column("image_items", "enhancement_stage")
