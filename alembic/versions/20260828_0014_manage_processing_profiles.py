"""manage filter and beautify profiles

Revision ID: 20260828_0014
Revises: 20260826_0013
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260828_0014"
down_revision: str | None = "20260826_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "processing_profiles",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("profile_type", sa.String(20), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("profile_type", "name", name="uq_processing_profiles_type_name"),
    )
    op.create_index("ix_processing_profiles_profile_type", "processing_profiles", ["profile_type"])
    op.create_index("ix_processing_profiles_status", "processing_profiles", ["status"])
    for table in ("image_jobs", "upload_batches"):
        op.add_column(table, sa.Column("filter_profile_snapshot", sa.JSON(), nullable=True))
        op.add_column(table, sa.Column("beautify_profile_snapshot", sa.JSON(), nullable=True))


def downgrade() -> None:
    for table in ("upload_batches", "image_jobs"):
        op.drop_column(table, "beautify_profile_snapshot")
        op.drop_column(table, "filter_profile_snapshot")
    op.drop_table("processing_profiles")
