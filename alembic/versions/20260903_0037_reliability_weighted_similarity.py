"""add reliability-weighted similarity audit fields

Revision ID: 20260903_0037
Revises: 20260903_0036
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0037"
down_revision: str | None = "20260903_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "image_similarity_matches",
        sa.Column(
            "score_version",
            sa.String(length=40),
            nullable=False,
            server_default="legacy_v2",
        ),
    )
    op.add_column(
        "image_similarity_matches",
        sa.Column("feature_reliability", sa.Float(), nullable=True),
    )
    op.add_column(
        "image_similarity_matches",
        sa.Column("candidate_margin", sa.Float(), nullable=True),
    )
    op.add_column(
        "image_similarity_matches",
        sa.Column(
            "field_scores",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
    )


def downgrade() -> None:
    op.drop_column("image_similarity_matches", "field_scores")
    op.drop_column("image_similarity_matches", "candidate_margin")
    op.drop_column("image_similarity_matches", "feature_reliability")
    op.drop_column("image_similarity_matches", "score_version")
