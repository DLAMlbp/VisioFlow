"""add EXIF orientation to image items

Revision ID: 20260819_0002
Revises: 20260819_0001
Create Date: 2026-08-19
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260819_0002"
down_revision: Union[str, None] = "20260819_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("image_items", sa.Column("exif_orientation", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("image_items", "exif_orientation")
