import importlib.util
from pathlib import Path

import sqlalchemy as sa


def test_pending_tag_reviews_are_finalized_as_binary_decisions(monkeypatch) -> None:
    migration_path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "20260910_0046_remove_manual_tag_review.py"
    )
    spec = importlib.util.spec_from_file_location("remove_manual_tag_review", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    groups = sa.Table(
        "library_asset_groups",
        metadata,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tags", sa.JSON()),
    )
    assets = sa.Table(
        "library_assets",
        metadata,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("group_id", sa.String()),
    )
    matches = sa.Table(
        "image_similarity_matches",
        metadata,
        sa.Column("image_id", sa.String(), primary_key=True),
        sa.Column("matched_asset_id", sa.String()),
        sa.Column("matched_tags_snapshot", sa.JSON()),
        sa.Column("decision", sa.String()),
        sa.Column("message", sa.String()),
        sa.Column("updated_at", sa.DateTime()),
    )
    ai_tags = sa.Table(
        "image_ai_tags",
        metadata,
        sa.Column("image_id", sa.String(), primary_key=True),
        sa.Column("tag_json", sa.JSON()),
        sa.Column("updated_at", sa.DateTime()),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(groups.insert(), {"id": "grp_1", "tags": ["施工报价"]})
        connection.execute(assets.insert(), {"id": "ast_1", "group_id": "grp_1"})
        connection.execute(
            matches.insert(),
            [
                {
                    "image_id": "img_matched",
                    "matched_asset_id": "ast_1",
                    "matched_tags_snapshot": [],
                    "decision": "pending_review",
                    "message": "等待人工确认",
                },
                {
                    "image_id": "img_unmatched",
                    "matched_asset_id": None,
                    "matched_tags_snapshot": [],
                    "decision": "pending_review",
                    "message": "等待人工确认",
                },
            ],
        )
        connection.execute(
            ai_tags.insert(),
            [
                {"image_id": "img_matched", "tag_json": {"candidate_tags": ["施工报价"]}},
                {"image_id": "img_unmatched", "tag_json": None},
            ],
        )
        monkeypatch.setattr(migration.op, "get_bind", lambda: connection)

        migration.upgrade()

        finalized = {
            row.image_id: row
            for row in connection.execute(sa.select(matches)).mappings().all()
        }
        finalized_ai_tags = {
            row.image_id: row.tag_json
            for row in connection.execute(sa.select(ai_tags)).mappings().all()
        }

    assert finalized["img_matched"].decision == "matched"
    assert finalized["img_matched"].matched_tags_snapshot == ["施工报价"]
    assert finalized_ai_tags["img_matched"]["tags"] == ["施工报价"]
    assert finalized_ai_tags["img_matched"]["candidate_tags"] == []
    assert finalized["img_unmatched"].decision == "unmatched"
    assert finalized["img_unmatched"].matched_asset_id is None
    assert finalized_ai_tags["img_unmatched"]["tags"] == []
