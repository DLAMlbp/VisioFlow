"""add enhanced image metrics

Revision ID: 20260820_0008
Revises: 20260820_0007
Create Date: 2026-08-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260820_0008"
down_revision: Union[str, None] = "20260820_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("image_results", sa.Column("enhanced_metrics_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("image_results", "enhanced_metrics_json")
