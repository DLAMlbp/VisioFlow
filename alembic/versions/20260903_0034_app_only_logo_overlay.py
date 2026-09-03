"""target only APP text with the transparent Xiaodang overlay

Revision ID: 20260903_0034
Revises: 20260903_0033
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260903_0034"
down_revision: str | None = "20260903_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESCRIPTION = "保留当家品牌名称，仅使用透明小当图标遮挡APP字样；左下角水印放行并去除；品牌地膜达到75%判定不合格"
INSTRUCTION = """1. 图片左下角拍摄水印允许通过筛选，通过后自动去除。
2. 当家APP Logo保留“当家”和左侧品牌图形，只使用透明小当图标遮挡英文APP。
3. 包含当家APP Logo的地面保护膜占整张图片75%及以上时判定不合格；接近阈值或判断不确定时人工复核。"""
OLD_DESCRIPTION = "左下角水印放行并去除，目标Logo用小当图标遮挡，当家品牌地膜占比达到75%判定不合格"
OLD_INSTRUCTION = """1. 图片左下角拍摄水印允许通过筛选，通过后自动去除。
2. 所有图片中的当家APP或平台Logo使用小当图标完整遮挡。
3. 包含当家APP Logo的地面保护膜占整张图片75%及以上时判定不合格；接近阈值或判断不确定时人工复核。"""


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            UPDATE processing_profiles
            SET description=:description,
                instruction=:instruction,
                config_json=jsonb_set(
                    jsonb_set(
                        jsonb_set(
                            config_json::jsonb,
                            '{description}',
                            to_jsonb(CAST(:description AS text))
                        ),
                        '{logo,target_component}',
                        '"app_text"'::jsonb
                    ),
                    '{logo,overlay_asset_id}',
                    '"xiaodang_cutout_v1"'::jsonb
                )::json,
                version=2,
                updated_at=now()
            WHERE id='redaction_default_v1'
              AND profile_type='redaction'
              AND version=1
            """
        ),
        {"description": DESCRIPTION, "instruction": INSTRUCTION},
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            UPDATE processing_profiles
            SET description=:description,
                instruction=:instruction,
                config_json=((config_json::jsonb - 'logo') || jsonb_build_object(
                        'logo',
                        ((config_json::jsonb -> 'logo') - 'target_component')
                        || jsonb_build_object('overlay_asset_id', 'xiaodang_v1')
                    ))::json,
                version=1,
                updated_at=now()
            WHERE id='redaction_default_v1'
              AND profile_type='redaction'
              AND version=2
            """
        ),
        {"description": OLD_DESCRIPTION, "instruction": OLD_INSTRUCTION},
    )
