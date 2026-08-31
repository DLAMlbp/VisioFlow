"""clarify completion classification evidence boundary

Revision ID: 20260831_0029
Revises: 20260831_0028
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260831_0029"
down_revision: str | None = "20260831_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_COMPLETED_NAME = "完工图片过滤"
_COMPLETED_DESCRIPTION = "先识别可见证据足以确认装修完成的室内图片，再按完工图要求过滤。"
_COMPLETED_RULE = (
    "图片可直接看到已完成装修的室内区域：无明显施工状态，主要硬装完成，并具备使用或展示条件。"
    "仅在这些完工证据实际可见时命中；不得仅依据画面整洁、局部成品或未见施工人员推断整体完工。"
)
_FALLBACK_NAME = "非完工及待确认图片过滤"
_FALLBACK_DESCRIPTION = (
    "接收施工、验收、局部记录及现有画面不足以确认整体完工的图片，再按对应要求过滤。"
)
_FALLBACK_RULE = (
    "兜底类别：施工中、半成品、隐蔽工程、材料或工具记录、局部未完成、拍摄范围不足以判断整体完工，"
    "以及其他未命中完工类别的图片。证据不足只表示无法判断，不等于确认尚未完工。"
)

_OLD_COMPLETED_DESCRIPTION = "先识别已完成装修的室内空间，再按完工图要求过滤。"
_OLD_COMPLETED_RULE = (
    "图片呈现已经完成装修的真实室内空间：无明显施工，主要硬装完整，"
    "可见成品空间效果并已具备使用或展示条件。"
)
_OLD_FALLBACK_NAME = "非完工图片过滤"
_OLD_FALLBACK_DESCRIPTION = "先识别未完成装修或完工证据不足的图片，再按施工图要求过滤。"
_OLD_FALLBACK_RULE = (
    "图片不满足完工类别，包含施工中、半成品、隐蔽工程、材料进场、局部未完成、"
    "完工证据不足或与完工空间无关的内容。"
)


def _update_profile(
    profile_id: str,
    *,
    name: str,
    description: str,
    classification_rule: str,
    version_delta: int,
) -> None:
    op.execute(
        f"""
        UPDATE processing_profiles
        SET
            name = $value${name}$value$,
            description = $value${description}$value$,
            instruction = $value${classification_rule}$value$,
            version = greatest(version + ({version_delta}), 1),
            config_json = jsonb_set(
                jsonb_set(
                    config_json::jsonb,
                    '{{classification_rule}}',
                    to_jsonb($value${classification_rule}$value$::text),
                    true
                ),
                '{{version}}',
                to_jsonb(greatest(version + ({version_delta}), 1)),
                true
            )::json
        WHERE id = $value${profile_id}$value$
          AND profile_type = 'standard'
        """
    )


def upgrade() -> None:
    _update_profile(
        "standard_completed_v1",
        name=_COMPLETED_NAME,
        description=_COMPLETED_DESCRIPTION,
        classification_rule=_COMPLETED_RULE,
        version_delta=1,
    )
    _update_profile(
        "standard_non_completed_v1",
        name=_FALLBACK_NAME,
        description=_FALLBACK_DESCRIPTION,
        classification_rule=_FALLBACK_RULE,
        version_delta=1,
    )


def downgrade() -> None:
    _update_profile(
        "standard_completed_v1",
        name=_COMPLETED_NAME,
        description=_OLD_COMPLETED_DESCRIPTION,
        classification_rule=_OLD_COMPLETED_RULE,
        version_delta=-1,
    )
    _update_profile(
        "standard_non_completed_v1",
        name=_OLD_FALLBACK_NAME,
        description=_OLD_FALLBACK_DESCRIPTION,
        classification_rule=_OLD_FALLBACK_RULE,
        version_delta=-1,
    )
