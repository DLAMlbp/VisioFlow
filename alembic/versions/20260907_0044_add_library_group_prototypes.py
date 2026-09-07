"""add material-group prototype marker

Revision ID: 20260907_0044
Revises: 20260904_0043
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260907_0044"
down_revision: str | None = "20260904_0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "library_asset_groups",
        sa.Column(
            "prototype_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "library_assets",
        sa.Column(
            "is_group_prototype",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index(
        "ix_library_assets_is_group_prototype",
        "library_assets",
        ["is_group_prototype"],
    )
    # Keep every existing group immediately matchable without making the first
    # online requests scan the entire library. Maintenance replaces this
    # deterministic seed with diversity-selected representatives.
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY group_id ORDER BY created_at, id
                   ) AS position
            FROM library_assets
            WHERE status = 'active' AND embedding IS NOT NULL
        )
        UPDATE library_assets AS asset
        SET is_group_prototype = TRUE
        FROM ranked
        WHERE asset.id = ranked.id AND ranked.position <= 6
        """
    )


def downgrade() -> None:
    op.drop_index("ix_library_assets_is_group_prototype", table_name="library_assets")
    op.drop_column("library_assets", "is_group_prototype")
    op.drop_column("library_asset_groups", "prototype_version")
