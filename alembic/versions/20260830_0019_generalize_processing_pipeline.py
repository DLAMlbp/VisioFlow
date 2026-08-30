"""add durable processing pipeline configuration and library matching scope

Revision ID: 20260830_0019
Revises: 20260830_0018
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260830_0019"
down_revision: str | None = "20260830_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table_name in ("image_jobs", "upload_batches"):
        for column_name in ("filter_enabled", "beautify_enabled", "similarity_enabled"):
            op.add_column(
                table_name,
                sa.Column(
                    column_name,
                    sa.Boolean(),
                    server_default=sa.true(),
                    nullable=False,
                ),
            )
        op.add_column(
            table_name,
            sa.Column(
                "unmatched_standard_policy",
                sa.String(length=16),
                server_default="reject",
                nullable=False,
            ),
        )
        op.add_column(
            table_name,
            sa.Column("library_scope_node_id", sa.String(length=40), nullable=True),
        )
        op.create_foreign_key(
            f"fk_{table_name}_library_scope_node_id",
            table_name,
            "library_tag_nodes",
            ["library_scope_node_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(
            f"ix_{table_name}_library_scope_node_id",
            table_name,
            ["library_scope_node_id"],
        )


def downgrade() -> None:
    for table_name in ("upload_batches", "image_jobs"):
        op.drop_index(f"ix_{table_name}_library_scope_node_id", table_name=table_name)
        op.drop_constraint(
            f"fk_{table_name}_library_scope_node_id", table_name, type_="foreignkey"
        )
        op.drop_column(table_name, "library_scope_node_id")
        op.drop_column(table_name, "unmatched_standard_policy")
        for column_name in ("similarity_enabled", "beautify_enabled", "filter_enabled"):
            op.drop_column(table_name, column_name)
