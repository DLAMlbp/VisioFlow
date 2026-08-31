"""pair each classification rule with one filter rule

Revision ID: 20260831_0026
Revises: 20260831_0025
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260831_0026"
down_revision: str | None = "20260831_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE processing_profiles
        SET config_json = (
            (config_json::jsonb - 'activation_rule')
            || jsonb_build_object(
                'classification_rule',
                COALESCE(
                    config_json ->> 'classification_rule',
                    config_json ->> 'activation_rule',
                    instruction
                )
            )
        )::json
        WHERE profile_type = 'standard'
        """
    )
    op.execute(
        """
        UPDATE processing_profiles
        SET
            description = CASE id
                WHEN 'standard_completed_v1' THEN
                    '先识别已完成装修的室内空间，再按完工图要求过滤。'
                WHEN 'standard_non_completed_v1' THEN
                    '先识别未完成装修或完工证据不足的图片，再按施工图要求过滤。'
                ELSE description
            END,
            instruction = CASE id
                WHEN 'standard_completed_v1' THEN
                    '图片呈现已经完成装修的真实室内空间：无明显施工，主要硬装完整，可见成品空间效果并已具备使用或展示条件。'
                WHEN 'standard_non_completed_v1' THEN
                    '图片不满足完工类别，包含施工中、半成品、隐蔽工程、材料进场、局部未完成、完工证据不足或与完工空间无关的内容。'
                ELSE instruction
            END,
            config_json = jsonb_set(
                config_json::jsonb,
                '{classification_rule}',
                to_jsonb(
                    CASE id
                        WHEN 'standard_completed_v1' THEN
                            '图片呈现已经完成装修的真实室内空间：无明显施工，主要硬装完整，可见成品空间效果并已具备使用或展示条件。'
                        WHEN 'standard_non_completed_v1' THEN
                            '图片不满足完工类别，包含施工中、半成品、隐蔽工程、材料进场、局部未完成、完工证据不足或与完工空间无关的内容。'
                        ELSE instruction
                    END
                )
            )::json
        WHERE id IN ('standard_completed_v1', 'standard_non_completed_v1')
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE processing_profiles
        SET config_json = (
            (config_json::jsonb - 'classification_rule')
            || jsonb_build_object(
                'activation_rule',
                COALESCE(config_json ->> 'classification_rule', instruction)
            )
        )::json
        WHERE profile_type = 'standard'
        """
    )
