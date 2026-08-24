"""create image metrics

Revision ID: 20260820_0004
Revises: 20260819_0003
Create Date: 2026-08-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260820_0004"
down_revision: Union[str, None] = "20260819_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "image_metrics",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("image_id", sa.String(length=40), nullable=False),
        sa.Column("sharpness_score", sa.Float(), nullable=False),
        sa.Column("exposure_score", sa.Float(), nullable=False),
        sa.Column("contrast_score", sa.Float(), nullable=False),
        sa.Column("noise_score", sa.Float(), nullable=False),
        sa.Column("raw_metrics_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["image_id"], ["image_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("image_id"),
    )
def downgrade() -> None:
    op.drop_table("image_metrics")
