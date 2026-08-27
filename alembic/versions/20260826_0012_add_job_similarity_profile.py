"""store the similarity profile selected for each image job

Revision ID: 20260826_0012
Revises: 20260826_0011
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260826_0012"
down_revision: str | None = "20260826_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "image_jobs",
        sa.Column(
            "similarity_profile_id",
            sa.String(length=80),
            nullable=False,
            server_default="library_similarity_v2",
        ),
    )
    op.alter_column("image_jobs", "similarity_profile_id", server_default=None)


def downgrade() -> None:
    op.drop_column("image_jobs", "similarity_profile_id")
