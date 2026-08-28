"""remove built-in business processing profiles

Revision ID: 20260828_0016
Revises: 20260828_0015
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260828_0016"
down_revision: str | None = "20260828_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LEGACY_PROFILE_VERSIONS = {
    "renovation_submission_v1": 7,
    "renovation_natural_v1": 5,
}


def upgrade() -> None:
    profiles = sa.table(
        "processing_profiles",
        sa.column("id", sa.String()),
        sa.column("version", sa.Integer()),
        sa.column("status", sa.String()),
    )
    for profile_id, built_in_version in LEGACY_PROFILE_VERSIONS.items():
        op.execute(
            profiles.update()
            .where(
                profiles.c.id == profile_id,
                profiles.c.version <= built_in_version,
            )
            .values(status="inactive")
        )


def downgrade() -> None:
    profiles = sa.table(
        "processing_profiles",
        sa.column("id", sa.String()),
        sa.column("version", sa.Integer()),
        sa.column("status", sa.String()),
    )
    for profile_id, built_in_version in LEGACY_PROFILE_VERSIONS.items():
        op.execute(
            profiles.update()
            .where(
                profiles.c.id == profile_id,
                profiles.c.version <= built_in_version,
            )
            .values(status="active")
        )
