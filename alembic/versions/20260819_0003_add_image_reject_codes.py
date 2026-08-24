"""add image rejection codes

Revision ID: 20260819_0003
Revises: 20260819_0002
Create Date: 2026-08-19
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260819_0003"
down_revision: Union[str, None] = "20260819_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("image_items", sa.Column("reject_codes", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("image_items", "reject_codes")
