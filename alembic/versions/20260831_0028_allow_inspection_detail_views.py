"""allow recognizable inspection and craftsmanship detail views

Revision ID: 20260831_0028
Revises: 20260831_0027
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260831_0028"
down_revision: str | None = "20260831_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_OLD_PASS_RULE = (
    "拍摄主体居中或占据画面主要位置，拍摄角度为平视常规视角，可完整展示施工全貌或关键工艺细节，"
    "画面平整无严重倾斜，具备完整空间视角，可清晰识别场景空间结构。"
)
_NEW_PASS_RULE = (
    "拍摄主体居中或占据画面主要位置，画面平整且无严重倾斜；满足以下任一条件即可："
    "能够展示施工空间、区域全貌或场景结构；或者能够清楚展示关键工艺、施工节点、验收对象、工具及结果。"
    "节点验收和关键工艺特写不强制同时出现地面、墙面和顶面。"
)
_OLD_REJECT_THREE = "3. 大角度仰拍、俯拍，仅聚焦顶面、地面，缺失墙面和完整空间；"
_NEW_REJECT_THREE = (
    "3. 大角度仰拍、俯拍且仅聚焦顶面或地面，同时无法识别有效施工节点、工艺、验收对象或记录结果；"
)
_OLD_REJECT_FOUR = (
    "4. 极度局部特写、仅拍摄房间角落/小块墙体/零碎管件，无完整空间视角，无法判断整体施工场景；"
)
_NEW_REJECT_FOUR = (
    "4. 极度局部特写且既无周边施工上下文，也无法识别具体施工对象、部位、工艺或验收结果；"
)
_BOUNDARY = (
    "\n构图判定边界：完整空间记录与可识别的关键工艺/节点验收特写满足任一即可。"
    "不得仅因缺少完整房间视角而拒绝能够明确表达施工节点、工艺细节或验收结果的图片。"
)


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE processing_profiles
        SET
            version = version + 1,
            config_json = jsonb_set(
                jsonb_set(
                    config_json::jsonb,
                    '{{filter_rule}}',
                    to_jsonb(
                        replace(
                            replace(
                                replace(config_json ->> 'filter_rule',
                                    $rule${_OLD_PASS_RULE}$rule$,
                                    $rule${_NEW_PASS_RULE}$rule$),
                                $rule${_OLD_REJECT_THREE}$rule$,
                                $rule${_NEW_REJECT_THREE}$rule$),
                            $rule${_OLD_REJECT_FOUR}$rule$,
                            $rule${_NEW_REJECT_FOUR}$rule$)
                        || $rule${_BOUNDARY}$rule$
                    ),
                    true
                ),
                '{{version}}',
                to_jsonb(version + 1),
                true
            )::json
        WHERE id = 'standard_non_completed_v1'
          AND profile_type = 'standard'
          AND position('构图判定边界：完整空间记录与可识别的关键工艺/节点验收特写满足任一即可' in config_json ->> 'filter_rule') = 0
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE processing_profiles
        SET
            version = greatest(version - 1, 1),
            config_json = jsonb_set(
                jsonb_set(
                    config_json::jsonb,
                    '{{filter_rule}}',
                    to_jsonb(
                        replace(
                            replace(
                                replace(
                                    replace(config_json ->> 'filter_rule',
                                        $rule${_NEW_PASS_RULE}$rule$,
                                        $rule${_OLD_PASS_RULE}$rule$),
                                    $rule${_NEW_REJECT_THREE}$rule$,
                                    $rule${_OLD_REJECT_THREE}$rule$),
                                $rule${_NEW_REJECT_FOUR}$rule$,
                                $rule${_OLD_REJECT_FOUR}$rule$),
                            $rule${_BOUNDARY}$rule$,
                            ''
                        )
                    ),
                    true
                ),
                '{{version}}',
                to_jsonb(greatest(version - 1, 1)),
                true
            )::json
        WHERE id = 'standard_non_completed_v1'
          AND profile_type = 'standard'
          AND position('构图判定边界：完整空间记录与可识别的关键工艺/节点验收特写满足任一即可' in config_json ->> 'filter_rule') > 0
        """
    )
