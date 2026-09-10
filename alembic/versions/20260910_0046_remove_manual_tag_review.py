"""remove manual tag review state

Revision ID: 20260910_0046
Revises: 20260910_0045
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260910_0046"
down_revision: str | None = "20260910_0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    matches = sa.table(
        "image_similarity_matches",
        sa.column("image_id", sa.String()),
        sa.column("matched_asset_id", sa.String()),
        sa.column("matched_tags_snapshot", sa.JSON()),
        sa.column("decision", sa.String()),
        sa.column("message", sa.String()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    assets = sa.table(
        "library_assets",
        sa.column("id", sa.String()),
        sa.column("group_id", sa.String()),
    )
    groups = sa.table(
        "library_asset_groups",
        sa.column("id", sa.String()),
        sa.column("tags", sa.JSON()),
    )
    ai_tags = sa.table(
        "image_ai_tags",
        sa.column("image_id", sa.String()),
        sa.column("tag_json", sa.JSON()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )

    pending_rows = connection.execute(
        sa.select(
            matches.c.image_id,
            matches.c.matched_asset_id,
            groups.c.tags.label("group_tags"),
        )
        .select_from(
            matches.outerjoin(
                assets, matches.c.matched_asset_id == assets.c.id
            ).outerjoin(groups, assets.c.group_id == groups.c.id)
        )
        .where(matches.c.decision == "pending_review")
    ).mappings().all()

    for row in pending_rows:
        inherited_tags = list(row["group_tags"] or [])
        matched = bool(row["matched_asset_id"] and inherited_tags)
        final_tags = inherited_tags if matched else []
        final_decision = "matched" if matched else "unmatched"
        message = (
            "已匹配到相似图片素材"
            if matched
            else "未识别到相似的图片素材"
        )
        connection.execute(
            matches.update()
            .where(matches.c.image_id == row["image_id"])
            .values(
                matched_asset_id=row["matched_asset_id"] if matched else None,
                matched_tags_snapshot=final_tags,
                decision=final_decision,
                message=message,
                updated_at=sa.func.now(),
            )
        )

        existing_tag_row = connection.execute(
            sa.select(ai_tags.c.tag_json).where(ai_tags.c.image_id == row["image_id"])
        ).mappings().first()
        if existing_tag_row is not None:
            tag_json = dict(existing_tag_row["tag_json"] or {})
            tag_json.update(
                {
                    "tags": final_tags,
                    "categories": {"素材库标签": final_tags} if matched else {},
                    "candidate_tags": [],
                }
            )
            connection.execute(
                ai_tags.update()
                .where(ai_tags.c.image_id == row["image_id"])
                .values(tag_json=tag_json, updated_at=sa.func.now())
            )


def downgrade() -> None:
    # The former pending state represented an operator decision that no longer exists;
    # finalized binary decisions cannot be reconstructed safely.
    pass
