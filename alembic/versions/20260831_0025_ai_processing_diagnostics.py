"""add routed processing response diagnostics

Revision ID: 20260831_0025
Revises: 20260831_0024
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260831_0025"
down_revision: str | None = "20260831_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "image_items",
        sa.Column("ai_processing_diagnostic_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("image_items", "ai_processing_diagnostic_json")
