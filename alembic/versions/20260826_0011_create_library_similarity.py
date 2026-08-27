"""create library tags, assets, and similarity matches

Revision ID: 20260826_0011
Revises: 20260824_0010
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision: str = "20260826_0011"
down_revision: str | None = "20260824_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "library_tag_nodes",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("parent_id", sa.String(length=40), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["library_tag_nodes.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("parent_id", "name", name="uq_library_tag_nodes_parent_name"),
    )
    op.create_index("ix_library_tag_nodes_parent_id", "library_tag_nodes", ["parent_id"])
    op.create_index("ix_library_tag_nodes_status", "library_tag_nodes", ["status"])

    op.create_table(
        "library_assets",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("original_object_key", sa.String(length=1024), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("leaf_tag_node_id", sa.String(length=40), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("phash", sa.String(length=64), nullable=True),
        sa.Column("analysis_json", sa.JSON(), nullable=True),
        sa.Column("embedding", Vector(512), nullable=True),
        sa.Column("embedding_version", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["leaf_tag_node_id"], ["library_tag_nodes.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("original_object_key"),
    )
    op.create_index("ix_library_assets_leaf_tag_node_id", "library_assets", ["leaf_tag_node_id"])
    op.create_index("ix_library_assets_sha256", "library_assets", ["sha256"])
    op.create_index("ix_library_assets_phash", "library_assets", ["phash"])
    op.create_index("ix_library_assets_status", "library_assets", ["status"])
    op.execute(
        "CREATE INDEX ix_library_assets_embedding_hnsw ON library_assets "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    op.create_table(
        "image_similarity_matches",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("image_id", sa.String(length=40), nullable=False),
        sa.Column("matched_asset_id", sa.String(length=40), nullable=True),
        sa.Column("matched_tag_path_snapshot", sa.JSON(), nullable=False),
        sa.Column("similarity_score", sa.Float(), nullable=True),
        sa.Column("feature_score", sa.Float(), nullable=True),
        sa.Column("final_score", sa.Float(), nullable=True),
        sa.Column("decision", sa.String(length=24), nullable=False),
        sa.Column("message", sa.String(length=200), nullable=False),
        sa.Column("candidate_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["image_id"], ["image_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["matched_asset_id"], ["library_assets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("image_id"),
    )
    op.create_index("ix_image_similarity_matches_decision", "image_similarity_matches", ["decision"])
    op.create_index("ix_image_similarity_matches_matched_asset_id", "image_similarity_matches", ["matched_asset_id"])


def downgrade() -> None:
    op.drop_table("image_similarity_matches")
    op.drop_index("ix_library_assets_embedding_hnsw", table_name="library_assets")
    op.drop_table("library_assets")
    op.drop_table("library_tag_nodes")
