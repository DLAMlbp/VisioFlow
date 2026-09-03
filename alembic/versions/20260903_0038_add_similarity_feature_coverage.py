"""add similarity feature coverage audit field

Revision ID: 20260903_0038
Revises: 20260903_0037
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0038"
down_revision: str | None = "20260903_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "image_similarity_matches",
        sa.Column("feature_coverage", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("image_similarity_matches", "feature_coverage")
