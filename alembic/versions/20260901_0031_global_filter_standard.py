"""separate global filtering from category standards

Revision ID: 20260901_0031
Revises: 20260831_0030
"""

from collections.abc import Sequence
import json

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_0031"
down_revision: str | None = "20260831_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


GLOBAL_RULE = """无论图片属于哪一种分类，先执行以下全局审核；任意一项明确不合格时必须淘汰：
1. 图片必须能够正常使用。图片损坏、主体无法识别、严重拉伸或压扁、极端超长超窄、严重压缩像素化均不合格。
2. 标准宽高比为3:4，优先使用后端提供的width、height和aspect_ratio；宽高比0.74至0.76视为3:4，明显偏离（如1:1、16:9或极端长条）不合格。
3. 严重虚焦、严重运动拖影、主体无法辨认、整张严重过曝或欠曝均不合格。轻微噪点、轻微压缩、普通手机画质、旧房环境偏暗但主体清楚，不得直接淘汰。
4. 明确的第三方装修公司名称或Logo、第三方电话微信二维码、抖音快手小红书等平台水印、明显影响使用的推广水印均不合格。
5. 正常小区名称、楼栋名称、门牌、道路名称和普通建筑文字允许保留。仅出现疑似号码但不能确认属于商业推广时，不得直接淘汰。
6. AI生成图、效果图或3D渲染图冒充真实照片，以及色情低俗、暴力血腥、违法违规或明显政治敏感内容，必须淘汰。
必须逐项给出审核维度和可见证据；证据不足时不得猜测淘汰。全局全部通过后，才能执行当前分类专属过滤规则。"""

FALLBACK_CLASSIFICATION = """仅当图片不明确命中其他任何分类时使用。图片仍与装修、旧房、施工、完工展示、报价、业主反馈或装修从业者内容相关，但现有分类无法准确覆盖。"""
FALLBACK_FILTER = """保留与装修业务明确相关、主体可辨且具有记录或展示价值的真实图片。不得仅因旧、破、脏、乱而淘汰；这些可能是真实旧房或施工状态。全局过滤标准已负责比例、严重画质问题、第三方商业信息、水印和内容安全，本规则只审核装修相关性与主体是否具有有效信息。"""


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE processing_profiles SET status='inactive' "
            "WHERE profile_type='filter' AND status='active'"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE processing_profiles SET status='inactive' "
            "WHERE profile_type='standard' AND name IN "
            "('总体审核原则','硬性淘汰标准','最核心的一条规则','空间图片标签规则')"
        )
    )
    global_config = {
        "id": "global_filter_v1",
        "version": 1,
        "description": "所有图片必经的比例、画质、商业信息与内容安全审核",
    }
    bind.execute(
        sa.text(
            """
            INSERT INTO processing_profiles (
                id, profile_type, name, description, instruction,
                config_json, version, status
            ) VALUES (
                'global_filter_v1', 'filter', '全局硬性过滤标准',
                '所有图片先执行全局审核，通过后再执行分类专属过滤',
                :instruction, CAST(:config AS json), 1, 'active'
            )
            ON CONFLICT (id) DO UPDATE SET
                name=EXCLUDED.name,
                description=EXCLUDED.description,
                instruction=EXCLUDED.instruction,
                config_json=EXCLUDED.config_json,
                version=processing_profiles.version + 1,
                status='active'
            """
        ),
        {"instruction": GLOBAL_RULE, "config": json.dumps(global_config, ensure_ascii=False)},
    )
    fallback_config = {
        "id": "standard_other_renovation_v1",
        "name": "其他装修相关图片",
        "version": 1,
        "description": "未命中明确分类时使用的装修相关兜底分类",
        "classification_rule": FALLBACK_CLASSIFICATION,
        "filter_rule": FALLBACK_FILTER,
        "priority": 0,
        "is_fallback": True,
    }
    bind.execute(
        sa.text(
            """
            INSERT INTO processing_profiles (
                id, profile_type, name, description, instruction,
                config_json, version, status
            ) VALUES (
                'standard_other_renovation_v1', 'standard', '其他装修相关图片',
                '未命中明确分类时使用的装修相关兜底分类',
                :instruction, CAST(:config AS json), 1, 'active'
            )
            ON CONFLICT (id) DO UPDATE SET
                name=EXCLUDED.name,
                description=EXCLUDED.description,
                instruction=EXCLUDED.instruction,
                config_json=EXCLUDED.config_json,
                version=processing_profiles.version + 1,
                status='active'
            """
        ),
        {
            "instruction": FALLBACK_CLASSIFICATION,
            "config": json.dumps(fallback_config, ensure_ascii=False),
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM processing_profiles WHERE id IN "
            "('global_filter_v1','standard_other_renovation_v1')"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE processing_profiles SET status='active' "
            "WHERE profile_type='filter' OR name IN "
            "('总体审核原则','硬性淘汰标准','最核心的一条规则','空间图片标签规则')"
        )
    )
