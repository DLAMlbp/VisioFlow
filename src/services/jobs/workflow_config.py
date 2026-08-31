from __future__ import annotations

from src.core.config import Settings

REQUIRED_WORKFLOW_SWITCHES: tuple[tuple[str, str], ...] = (
    ("completion_routing_enabled", "完工分类与双路由过滤"),
    ("post_filter_beautify_plan_enabled", "过滤后 AI 美化规划"),
    ("library_image_only_matching_enabled", "图片向量与大模型内容特征混合匹配"),
    ("library_only_tags_enabled", "仅继承素材库人工标签"),
)


def disabled_required_workflow_stages(settings: Settings) -> list[str]:
    return [
        label
        for field_name, label in REQUIRED_WORKFLOW_SWITCHES
        if not bool(getattr(settings, field_name))
    ]


def required_workflow_error(settings: Settings) -> str | None:
    disabled = disabled_required_workflow_stages(settings)
    if not disabled:
        return None
    return "必需工作流已关闭：" + "、".join(disabled)
