"""replace library tag trees with flat asset groups

Revision ID: 20260830_0021
Revises: 20260830_0020
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260830_0021"
down_revision: str | None = "20260830_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "library_asset_groups",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("tag_key", sa.String(length=2000), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tag_key", name="uq_library_asset_groups_tag_key"),
    )
    op.create_index("ix_library_asset_groups_status", "library_asset_groups", ["status"])

    # Every legacy leaf that owns assets becomes one flat group.  Its former
    # root-to-leaf path is retained as the group's ordered tag list.
    op.execute(
        """
        WITH RECURSIVE paths AS (
            SELECT id, parent_id, ARRAY[name]::varchar[] AS tags
            FROM library_tag_nodes
            WHERE parent_id IS NULL
            UNION ALL
            SELECT child.id, child.parent_id, paths.tags || child.name
            FROM library_tag_nodes AS child
            JOIN paths ON child.parent_id = paths.id
        ), grouped AS (
            SELECT
                node.id,
                paths.tags,
                node.sort_order,
                node.status,
                node.created_at,
                node.updated_at,
                lower(array_to_string(paths.tags, E'\\x1f')) AS base_key,
                row_number() OVER (
                    PARTITION BY lower(array_to_string(paths.tags, E'\\x1f'))
                    ORDER BY node.id
                ) AS duplicate_number
            FROM library_tag_nodes AS node
            JOIN paths ON paths.id = node.id
            WHERE EXISTS (
                SELECT 1 FROM library_assets AS asset
                WHERE asset.leaf_tag_node_id = node.id
            )
        )
        INSERT INTO library_asset_groups (
            id, tags, tag_key, sort_order, status, created_at, updated_at
        )
        SELECT
            'grp_' || md5(id),
            to_json(tags),
            CASE
                WHEN duplicate_number = 1 THEN base_key
                ELSE base_key || E'\\x1flegacy-' || duplicate_number::text
            END,
            sort_order,
            status,
            created_at,
            updated_at
        FROM grouped
        """
    )

    op.add_column("library_assets", sa.Column("group_id", sa.String(length=40), nullable=True))
    op.execute(
        """
        UPDATE library_assets
        SET group_id = 'grp_' || md5(leaf_tag_node_id)
        """
    )
    op.alter_column("library_assets", "group_id", nullable=False)
    op.create_foreign_key(
        "fk_library_assets_group_id",
        "library_assets",
        "library_asset_groups",
        ["group_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_library_assets_group_id", "library_assets", ["group_id"])

    for table_name in ("image_jobs", "upload_batches"):
        op.drop_index(f"ix_{table_name}_library_scope_node_id", table_name=table_name)
        op.drop_constraint(
            f"fk_{table_name}_library_scope_node_id", table_name, type_="foreignkey"
        )
        op.drop_column(table_name, "library_scope_node_id")

    op.drop_index("ix_library_assets_leaf_tag_node_id", table_name="library_assets")
    op.drop_constraint(
        "library_assets_leaf_tag_node_id_fkey", "library_assets", type_="foreignkey"
    )
    op.drop_column("library_assets", "leaf_tag_node_id")
    op.alter_column(
        "image_similarity_matches",
        "matched_tag_path_snapshot",
        new_column_name="matched_tags_snapshot",
    )

    op.drop_index("ix_library_tag_nodes_status", table_name="library_tag_nodes")
    op.drop_index("ix_library_tag_nodes_parent_id", table_name="library_tag_nodes")
    op.drop_table("library_tag_nodes")


def downgrade() -> None:
    op.create_table(
        "library_tag_nodes",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("parent_id", sa.String(length=40), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["parent_id"], ["library_tag_nodes.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("parent_id", "name", name="uq_library_tag_nodes_parent_name"),
    )
    op.create_index("ix_library_tag_nodes_parent_id", "library_tag_nodes", ["parent_id"])
    op.create_index("ix_library_tag_nodes_status", "library_tag_nodes", ["status"])
    op.execute(
        """
        INSERT INTO library_tag_nodes (
            id, parent_id, name, depth, sort_order, status, created_at, updated_at
        )
        SELECT
            'ltn_' || md5(id),
            NULL,
            left(array_to_string(ARRAY(SELECT json_array_elements_text(tags)), ' / '), 120),
            0,
            sort_order,
            status,
            created_at,
            updated_at
        FROM library_asset_groups
        """
    )

    op.add_column(
        "library_assets", sa.Column("leaf_tag_node_id", sa.String(length=40), nullable=True)
    )
    op.execute(
        """
        UPDATE library_assets
        SET leaf_tag_node_id = 'ltn_' || md5(group_id)
        """
    )
    op.alter_column("library_assets", "leaf_tag_node_id", nullable=False)
    op.create_foreign_key(
        "library_assets_leaf_tag_node_id_fkey",
        "library_assets",
        "library_tag_nodes",
        ["leaf_tag_node_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_library_assets_leaf_tag_node_id", "library_assets", ["leaf_tag_node_id"]
    )

    for table_name in ("image_jobs", "upload_batches"):
        op.add_column(
            table_name, sa.Column("library_scope_node_id", sa.String(length=40), nullable=True)
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
            f"ix_{table_name}_library_scope_node_id", table_name, ["library_scope_node_id"]
        )

    op.alter_column(
        "image_similarity_matches",
        "matched_tags_snapshot",
        new_column_name="matched_tag_path_snapshot",
    )
    op.drop_index("ix_library_assets_group_id", table_name="library_assets")
    op.drop_constraint("fk_library_assets_group_id", "library_assets", type_="foreignkey")
    op.drop_column("library_assets", "group_id")
    op.drop_index("ix_library_asset_groups_status", table_name="library_asset_groups")
    op.drop_table("library_asset_groups")
