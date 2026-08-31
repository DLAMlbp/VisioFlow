"""add post-filter beautify planning audit fields

Revision ID: 20260831_0023
Revises: 20260830_0022
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260831_0023"
down_revision: str | None = "20260830_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = (
        sa.Column("beautify_plan_status", sa.String(length=24), nullable=True),
        sa.Column("beautify_plan_json", sa.JSON(), nullable=True),
        sa.Column("beautify_plan_model", sa.String(length=120), nullable=True),
        sa.Column("beautify_plan_prompt_version", sa.String(length=80), nullable=True),
        sa.Column("beautify_plan_duration_ms", sa.Integer(), nullable=True),
        sa.Column("beautify_plan_error", sa.String(length=500), nullable=True),
        sa.Column("beautify_plan_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("beautify_plan_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    for column in columns:
        op.add_column("image_items", column)
    op.create_index(
        "ix_image_items_beautify_plan_status",
        "image_items",
        ["beautify_plan_status"],
    )
    op.get_bind().exec_driver_sql(
        """
        UPDATE processing_profiles
        SET description = '完工图片保留有辨识价值的真实成品空间和装修成果',
            instruction = '后端已判定为完工图片。保留能够清楚展示完整空间、主要区域或有辨识价值装修成果的真实照片；少量保护膜、包装箱、清理痕迹、家具或软装未完全进场可以保留，不得仅因缺少家具家电而拒绝。拒绝范围过小且无上下文的产品或构件特写、主体严重遮挡、没有成品空间展示价值、与完工事实明显冲突、效果图、图纸、商品图、室外图或无关图片。尺寸、损坏、重复和客观质量问题由本地技术预检负责。',
            config_json = json_build_object(
                'id', id,
                'version', version + 1,
                'description', '完工图片保留有辨识价值的真实成品空间和装修成果',
                'activation_rule', '后端已判定为完工，始终执行本标准',
                'filter_rule', '保留能够清楚展示完整空间、主要区域或有辨识价值装修成果的真实完工照片；允许少量保护膜、包装箱、清理痕迹以及家具软装未完全进场；拒绝无上下文特写、严重遮挡、缺少成品空间展示价值、分类事实冲突及无效无关图片',
                'priority', 200
            ),
            version = version + 1
        WHERE id = 'standard_completed_v1';
        """
    )
    op.get_bind().exec_driver_sql(
        """
        UPDATE processing_profiles
        SET description = '施工图片保留能够体现真实施工阶段、工艺节点或未完成部位的记录',
            instruction = '后端已判定为 construction 的真实室内施工照片。保留能够看出空间、施工阶段、施工节点、工艺过程或未完成部位的图片；毛坯、水电、泥木、瓦工、油漆、吊顶、柜体、洁具、设备安装和收尾阶段均可保留，材料工具、垃圾、保护措施、裸露管线和未完成表面本身不是拒绝理由。拒绝无法确认施工现场的商品式特写、范围过小且无施工上下文、严重模糊过暗或遮挡、效果图、图纸、室外图和无关图片。不得套用完工图的整洁和成品化要求。',
            config_json = json_build_object(
                'id', id,
                'version', version + 1,
                'description', '施工图片保留能够体现真实施工阶段、工艺节点或未完成部位的记录',
                'activation_rule', '后端已判定为施工中，始终执行本标准',
                'filter_rule', '保留能够体现真实空间、施工阶段、施工节点、工艺过程或未完成部位的室内施工记录；材料工具、垃圾、裸露管线和未完成表面不是拒绝理由；拒绝无施工上下文的商品式特写、不可判断图片及无效无关图片',
                'priority', 100
            ),
            version = version + 1
        WHERE id = 'standard_non_completed_v1';
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_image_items_beautify_plan_status", table_name="image_items"
    )
    for column_name in (
        "beautify_plan_completed_at",
        "beautify_plan_started_at",
        "beautify_plan_error",
        "beautify_plan_duration_ms",
        "beautify_plan_prompt_version",
        "beautify_plan_model",
        "beautify_plan_json",
        "beautify_plan_status",
    ):
        op.drop_column("image_items", column_name)
