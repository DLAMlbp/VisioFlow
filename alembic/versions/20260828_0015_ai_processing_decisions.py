"""store combined AI processing decisions

Revision ID: 20260828_0015
Revises: 20260828_0014
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260828_0015"
down_revision: str | None = "20260828_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("image_items", sa.Column("ai_processing_status", sa.String(24), nullable=True))
    op.create_index(
        "ix_image_items_ai_processing_status", "image_items", ["ai_processing_status"]
    )
    op.add_column("image_items", sa.Column("ai_processing_json", sa.JSON(), nullable=True))
    op.add_column("image_items", sa.Column("ai_processing_model", sa.String(120), nullable=True))
    op.add_column(
        "image_items", sa.Column("ai_processing_prompt_version", sa.String(80), nullable=True)
    )
    op.add_column("image_items", sa.Column("ai_processing_duration_ms", sa.Integer(), nullable=True))
    op.add_column("image_items", sa.Column("ai_processing_error", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("image_items", "ai_processing_error")
    op.drop_column("image_items", "ai_processing_duration_ms")
    op.drop_column("image_items", "ai_processing_prompt_version")
    op.drop_column("image_items", "ai_processing_model")
    op.drop_column("image_items", "ai_processing_json")
    op.drop_index("ix_image_items_ai_processing_status", table_name="image_items")
    op.drop_column("image_items", "ai_processing_status")
