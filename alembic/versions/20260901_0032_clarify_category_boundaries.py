"""clarify category boundaries after global filter split

Revision ID: 20260901_0032
Revises: 20260901_0031
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_0032"
down_revision: str | None = "20260901_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


G_BOUNDARY = """

【文字与商业信息边界】
正常小区名称、楼栋名称、门牌号、道路名称、SOHO、公寓、花园、家园等建筑标识允许保留。
只有明确属于第三方装修公司推广的名称、Logo、电话、微信或二维码才不合格。
仅出现数字、楼栋编号、门牌号或疑似号码，但无法确认属于装修商业推广时，必须通过本维度，不得猜测淘汰。"""

D_CLASSIFICATION = """
也包括日常施工记录：工长巡查、材料进场与验收、开工现场、单工种完工验收、主材安装与验收。"""

D_FILTER = """

【日常施工记录边界】
材料验收和单工种完工验收要求主体清楚、材料或施工成果可辨；开工现场、巡查工地和主材安装允许存在工具、材料、施工人员、少量建筑垃圾、未完成区域及施工痕迹。不得仅因真实施工状态不够整洁而淘汰。"""


def _append_rule(bind, name: str, field: str, suffix: str) -> None:
    bind.execute(
        sa.text(
            f"""
            UPDATE processing_profiles
            SET config_json = jsonb_set(
                    config_json::jsonb,
                    '{{{field}}}',
                    to_jsonb((COALESCE(config_json::jsonb->>'{field}', '') || :suffix)::text)
                )::json,
                version = version + 1,
                updated_at = now()
            WHERE profile_type='standard' AND name=:name AND status='active'
              AND position(:marker in COALESCE(config_json::jsonb->>'{field}', '')) = 0
            """
        ),
        {"name": name, "suffix": suffix, "marker": suffix.strip().splitlines()[0]},
    )


def upgrade() -> None:
    bind = op.get_bind()
    _append_rule(bind, "G类：老房子小区门头", "filter_rule", G_BOUNDARY)
    _append_rule(bind, "D类施工中", "classification_rule", D_CLASSIFICATION)
    _append_rule(bind, "D类施工中", "filter_rule", D_FILTER)
    bind.execute(
        sa.text(
            "UPDATE processing_profiles SET status='inactive', updated_at=now() "
            "WHERE profile_type='standard' AND name='日常施工记录' AND status='active'"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    for name, field, suffix in (
        ("G类：老房子小区门头", "filter_rule", G_BOUNDARY),
        ("D类施工中", "classification_rule", D_CLASSIFICATION),
        ("D类施工中", "filter_rule", D_FILTER),
    ):
        bind.execute(
            sa.text(
                f"""
                UPDATE processing_profiles
                SET config_json = jsonb_set(
                        config_json::jsonb,
                        '{{{field}}}',
                        to_jsonb(replace(COALESCE(config_json::jsonb->>'{field}', ''), :suffix, '')::text)
                    )::json,
                    version = version + 1,
                    updated_at = now()
                WHERE profile_type='standard' AND name=:name
                """
            ),
            {"name": name, "suffix": suffix},
        )
    bind.execute(
        sa.text(
            "UPDATE processing_profiles SET status='active', updated_at=now() "
            "WHERE profile_type='standard' AND name='日常施工记录'"
        )
    )
