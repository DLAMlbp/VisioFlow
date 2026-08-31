"""add fallback flag to paired processing standards

Revision ID: 20260831_0027
Revises: 20260831_0026
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260831_0027"
down_revision: str | None = "20260831_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE processing_profiles
        SET config_json = jsonb_set(
            config_json::jsonb,
            '{is_fallback}',
            'false'::jsonb,
            true
        )::json
        WHERE profile_type = 'standard'
        """
    )
    op.execute(
        """
        UPDATE processing_profiles
        SET config_json = jsonb_set(
            config_json::jsonb,
            '{is_fallback}',
            'true'::jsonb,
            true
        )::json
        WHERE id = 'standard_non_completed_v1'
          AND profile_type = 'standard'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE processing_profiles
        SET config_json = (config_json::jsonb - 'is_fallback')::json
        WHERE profile_type = 'standard'
        """
    )
