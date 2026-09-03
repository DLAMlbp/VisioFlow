"""add bounded enhancement recovery attempts

Revision ID: 20260903_0041
Revises: 20260903_0040
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260903_0041"
down_revision: str | None = "20260903_0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "image_items",
        sa.Column(
            "enhancement_recovery_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("image_items", "enhancement_recovery_attempts")
