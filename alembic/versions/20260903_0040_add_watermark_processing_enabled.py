"""add task-scoped watermark processing switch

Revision ID: 20260903_0040
Revises: 20260903_0039
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260903_0040"
down_revision: str | None = "20260903_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESCRIPTION = "左下角拍摄水印始终放行；任务开启水印处理时，仅使用透明小当图标遮挡英文APP；品牌地膜达到75%判定不合格"
INSTRUCTION = """1. 图片左下角拍摄水印允许通过筛选，不得仅因该水印判定不合格。
2. 任务关闭水印处理时完整保留拍摄水印；任务开启时只定位英文APP并使用透明小当图标遮挡，其他水印文字和画面保持不变。
3. 当家APP Logo保留“当家”和左侧品牌图形，只使用透明小当图标遮挡英文APP。
4. 包含当家APP Logo的地面保护膜占整张图片75%及以上时判定不合格；接近阈值或判断不确定时人工复核。"""
OLD_DESCRIPTION = "保留当家品牌名称，仅使用透明小当图标遮挡APP字样；左下角水印放行并去除；品牌地膜达到75%判定不合格"
OLD_INSTRUCTION = """1. 图片左下角拍摄水印允许通过筛选，通过后自动去除。
2. 当家APP Logo保留“当家”和左侧品牌图形，只使用透明小当图标遮挡英文APP。
3. 包含当家APP Logo的地面保护膜占整张图片75%及以上时判定不合格；接近阈值或判断不确定时人工复核。"""


def upgrade() -> None:
    for table_name in ("image_jobs", "upload_batches"):
        op.add_column(
            table_name,
            sa.Column(
                "watermark_processing_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            UPDATE processing_profiles
            SET description=:description,
                instruction=:instruction,
                config_json=jsonb_set(
                    config_json::jsonb,
                    '{version}',
                    '3'::jsonb
                )::json,
                version=3,
                updated_at=now()
            WHERE id='redaction_default_v1'
              AND profile_type='redaction'
              AND version=2
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
                config_json=jsonb_set(
                    config_json::jsonb,
                    '{version}',
                    '2'::jsonb
                )::json,
                version=2,
                updated_at=now()
            WHERE id='redaction_default_v1'
              AND profile_type='redaction'
              AND version=3
            """
        ),
        {"description": OLD_DESCRIPTION, "instruction": OLD_INSTRUCTION},
    )
    for table_name in ("upload_batches", "image_jobs"):
        op.drop_column(table_name, "watermark_processing_enabled")
