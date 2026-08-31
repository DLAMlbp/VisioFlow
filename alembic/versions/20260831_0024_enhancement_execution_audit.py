"""add enhancement execution audit

Revision ID: 20260831_0024
Revises: 20260831_0023
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260831_0024"
down_revision: str | None = "20260831_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "image_results",
        sa.Column("enhancement_audit_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("image_results", "enhancement_audit_json")
