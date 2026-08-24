"""add job ai tagging model snapshot

Revision ID: 20260824_0010
Revises: 20260822_0009
Create Date: 2026-08-24
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260824_0010"
down_revision: Union[str, None] = "20260822_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("image_jobs", sa.Column("ai_tagging_model", sa.String(length=120), nullable=True))


def downgrade() -> None:
    op.drop_column("image_jobs", "ai_tagging_model")
