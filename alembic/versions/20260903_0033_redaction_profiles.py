"""add versioned watermark and logo standards

Revision ID: 20260903_0033
Revises: 20260901_0032
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260903_0033"
down_revision: str | None = "20260901_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


DEFAULT_CONFIG = {
    "id": "redaction_default_v1",
    "version": 1,
    "description": "左下角水印放行并去除，目标Logo用小当图标遮挡，当家品牌地膜占比达到75%判定不合格",
    "watermark": {
        "enabled": True,
        "mode": "dangjia_bottom_left",
        "roi": [0.0, 0.84, 0.48, 1.0],
        "backend": "deblend_then_lama",
        "preserve_outside_roi": True,
        "max_modified_ratio": 0.50,
        "detection_threshold": 0.38,
        "inpaint_radius": 3,
        "roi_ocr_enabled": True,
        "allow_during_filter": True,
        "post_action": "remove",
    },
    "logo": {
        "enabled": True,
        "targets": ["dangjia_logo"],
        "confidence": 0.45,
        "nms_iou": 0.50,
        "box_expansion": 0.10,
        "mosaic_block_ratio": 0.16,
        "include_product_logos": False,
        "action": "overlay_asset",
        "overlay_asset_id": "xiaodang_v1",
        "overlay_scale": 1.12,
    },
    "branded_ground_film": {
        "enabled": True,
        "brand": "dangjia_app",
        "reject_coverage_gte": 0.75,
        "review_margin": 0.05,
        "min_confidence": 0.70,
        "uncertain_action": "manual_review",
    },
}

DEFAULT_INSTRUCTION = """1. 图片左下角拍摄水印允许通过筛选，通过后自动去除。
2. 所有图片中的当家APP或平台Logo使用小当图标完整遮挡。
3. 包含当家APP Logo的地面保护膜占整张图片75%及以上时判定不合格；接近阈值或判断不确定时人工复核。"""


def upgrade() -> None:
    for table in ("image_jobs", "upload_batches"):
        op.add_column(table, sa.Column("redaction_profile_id", sa.String(80), nullable=True))
        op.add_column(table, sa.Column("redaction_profile_snapshot", sa.JSON(), nullable=True))

    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            INSERT INTO processing_profiles (
                id, profile_type, name, description, instruction, config_json,
                version, status, created_at, updated_at
            ) VALUES (
                :id, 'redaction', :name, :description, :instruction,
                CAST(:config AS json), 1, 'active', now(), now()
            )
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {
            "id": "redaction_default_v1",
            "name": "当家水印与Logo标准",
            "description": DEFAULT_CONFIG["description"],
            "instruction": DEFAULT_INSTRUCTION,
            "config": json.dumps(DEFAULT_CONFIG, ensure_ascii=False),
        },
    )


def downgrade() -> None:
    op.execute("DELETE FROM processing_profiles WHERE id = 'redaction_default_v1'")
    for table in ("upload_batches", "image_jobs"):
        op.drop_column(table, "redaction_profile_snapshot")
        op.drop_column(table, "redaction_profile_id")
