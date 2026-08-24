"""create image results

Revision ID: 20260820_0005
Revises: 20260820_0004
Create Date: 2026-08-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260820_0005"
down_revision: Union[str, None] = "20260820_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "image_results",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("image_id", sa.String(length=40), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("final_score", sa.Float(), nullable=True),
        sa.Column("enhanced_object_key", sa.String(length=1024), nullable=True),
        sa.Column("reject_codes_json", sa.JSON(), nullable=True),
        sa.Column("reasons_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["image_id"], ["image_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("image_id"),
    )


def downgrade() -> None:
    op.drop_table("image_results")
