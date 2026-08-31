"""add completion classification and routed filtering

Revision ID: 20260830_0022
Revises: 20260830_0021
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260830_0022"
down_revision: str | None = "20260830_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table_name in ("image_jobs", "upload_batches"):
        op.add_column(
            table_name,
            sa.Column("routing_mode", sa.String(length=24), server_default="legacy", nullable=False),
        )
        for column_name in (
            "completion_profile_id",
            "completed_filter_profile_id",
            "non_completed_filter_profile_id",
        ):
            op.add_column(table_name, sa.Column(column_name, sa.String(length=80), nullable=True))
        for column_name in (
            "completion_profile_snapshot",
            "completed_filter_profile_snapshot",
            "non_completed_filter_profile_snapshot",
            "routing_policy_json",
        ):
            op.add_column(table_name, sa.Column(column_name, sa.JSON(), nullable=True))

    item_columns = (
        sa.Column("completion_status", sa.String(length=24), nullable=True),
        sa.Column("completion_label", sa.String(length=24), nullable=True),
        sa.Column("completion_subtype", sa.String(length=32), nullable=True),
        sa.Column("completion_confidence", sa.Float(), nullable=True),
        sa.Column("completion_json", sa.JSON(), nullable=True),
        sa.Column("completion_model", sa.String(length=120), nullable=True),
        sa.Column("completion_prompt_version", sa.String(length=80), nullable=True),
        sa.Column("completion_duration_ms", sa.Integer(), nullable=True),
        sa.Column("completion_error", sa.String(length=500), nullable=True),
        sa.Column("completion_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completion_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("routed_filter_profile_id", sa.String(length=80), nullable=True),
        sa.Column("routed_filter_profile_version", sa.Integer(), nullable=True),
        sa.Column("review_required", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("ai_processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ai_processing_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    for column in item_columns:
        op.add_column("image_items", column)
    op.create_index("ix_image_items_completion_status", "image_items", ["completion_status"])
    op.create_index("ix_image_items_completion_label", "image_items", ["completion_label"])
    op.create_index("ix_image_items_review_required", "image_items", ["review_required"])

    op.get_bind().exec_driver_sql(
        """
        INSERT INTO processing_profiles (
            id, profile_type, name, description, instruction, config_json, version, status
        ) VALUES
        (
            'completion_renovation_v1', 'completion', '装修完工状态分类',
            '根据可见事实逐图判断装修空间属于完工或非完工',
            '仅判断真实室内装修照片。完工必须同时满足：画面可判断主要空间、无明显施工、墙地顶和固定装修完成、存在成品空间证据且可使用或展示。效果图、图纸、室外或无关图片属于无效；范围过小、严重模糊、过暗或遮挡属于证据不足。',
            '{"id":"completion_renovation_v1","version":1,"description":"根据可见事实逐图判断装修空间属于完工或非完工"}'::json,
            1, 'active'
        ),
        (
            'standard_completed_v1', 'standard', '完工图片过滤',
            '完工图片只保留真实、清晰且能完整展示装修成果的照片',
            '后端已判定为完工图片，必须执行完工分支过滤',
            '{"id":"standard_completed_v1","version":1,"description":"完工图片只保留真实、清晰且能完整展示装修成果的照片","activation_rule":"后端已判定为完工，始终执行本标准","filter_rule":"保留真实室内完工照片，主体空间清晰可辨、装修成果展示充分且无严重遮挡；拒绝截图、拼图、效果图、明显模糊、过暗过曝、范围过小或无法体现完工成果的图片","priority":200}'::json,
            1, 'active'
        ),
        (
            'standard_non_completed_v1', 'standard', '非完工图片过滤',
            '非完工图片只保留真实、清晰且能体现施工状态的照片',
            '后端已判定为真实施工中的非完工图片，必须执行非完工分支过滤',
            '{"id":"standard_non_completed_v1","version":1,"description":"非完工图片只保留真实、清晰且能体现施工状态的照片","activation_rule":"后端已判定为施工中，始终执行本标准","filter_rule":"保留真实室内装修施工照片，施工区域和进度清晰可辨；拒绝无关图片、截图、效果图、严重模糊、过暗过曝、遮挡严重或无法体现施工状态的图片","priority":100}'::json,
            1, 'active'
        )
        ON CONFLICT (id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM processing_profiles
        WHERE id IN (
            'completion_renovation_v1',
            'standard_completed_v1',
            'standard_non_completed_v1'
        )
        """
    )
    for index_name in (
        "ix_image_items_review_required",
        "ix_image_items_completion_label",
        "ix_image_items_completion_status",
    ):
        op.drop_index(index_name, table_name="image_items")
    for column_name in (
        "ai_processing_completed_at",
        "ai_processing_started_at",
        "review_required",
        "routed_filter_profile_version",
        "routed_filter_profile_id",
        "completion_completed_at",
        "completion_started_at",
        "completion_error",
        "completion_duration_ms",
        "completion_prompt_version",
        "completion_model",
        "completion_json",
        "completion_confidence",
        "completion_subtype",
        "completion_label",
        "completion_status",
    ):
        op.drop_column("image_items", column_name)
    for table_name in ("upload_batches", "image_jobs"):
        for column_name in (
            "routing_policy_json",
            "non_completed_filter_profile_snapshot",
            "completed_filter_profile_snapshot",
            "completion_profile_snapshot",
            "non_completed_filter_profile_id",
            "completed_filter_profile_id",
            "completion_profile_id",
            "routing_mode",
        ):
            op.drop_column(table_name, column_name)
