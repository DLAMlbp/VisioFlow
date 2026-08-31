"""add URL integration contract and customer object-key passthrough

Revision ID: 20260831_0026
Revises: 20260831_0025
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260831_0026"
down_revision: str | None = "20260831_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "image_jobs",
        sa.Column(
            "callback_contract",
            sa.String(length=32),
            nullable=False,
            server_default="native_v1",
        ),
    )
    op.add_column(
        "image_items",
        sa.Column("client_object_key", sa.String(length=200), nullable=True),
    )
    op.create_index(
        "ix_image_items_client_object_key",
        "image_items",
        ["client_object_key"],
    )
    op.execute(
        """
        INSERT INTO processing_profiles (
            id, profile_type, name, description, instruction,
            config_json, version, status
        ) VALUES (
            'integration_natural_v1',
            'beautify',
            '第三方接口自然美化',
            '面向第三方 URL 接口的自然、克制型图片优化',
            '保持真实装修现场和原始构图，只做自然白平衡、适度提亮、轻度降噪、清晰度优化和方向校正，不添加、删除或替换画面内容。',
            '{
              "id":"integration_natural_v1",
              "version":1,
              "description":"自然、克制地改善装修图片观感，不改变真实内容",
              "brightness":1.03,
              "contrast":1.03,
              "color":1.02,
              "sharpness":1.08,
              "auto_white_balance":true,
              "white_balance_strength":0.45,
              "shadow_lift":0.08,
              "highlight_recovery":0.10,
              "denoise_strength":0.14,
              "local_tone_strength":0.14,
              "local_tone_clip_limit":1.4,
              "glare_reduction_strength":0.14,
              "local_clarity_strength":0.14,
              "auto_straighten":true,
              "max_straighten_degrees":3.0,
              "min_output_long_side":2048,
              "jpeg_quality":95
            }'::json,
            1,
            'active'
        )
        ON CONFLICT (id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM processing_profiles WHERE id = 'integration_natural_v1' AND version = 1"
    )
    op.drop_index("ix_image_items_client_object_key", table_name="image_items")
    op.drop_column("image_items", "client_object_key")
    op.drop_column("image_jobs", "callback_contract")
