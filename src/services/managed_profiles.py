from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Literal
from urllib.request import Request, urlopen
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings
from src.models.processing_profile import ProcessingProfile
from src.services.ai_model_config import load_ai_model_settings
from src.services.profiles import (
    BeautifyProfile,
    CompletionProfile,
    FilterProfile,
    ProcessingStandard,
    ProfileNotFoundError,
    RedactionProfile,
)

ProfileType = Literal["filter", "beautify", "redaction", "standard", "completion"]


class ManagedProfileError(Exception):
    pass


@dataclass(frozen=True)
class CompiledProfile:
    description: str
    config: dict[str, object]
    unsupported: list[str]


@dataclass(frozen=True)
class CompiledStandard:
    description: str
    config: dict[str, object]
    unsupported: list[str]


class ManagedProfileService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def list(self, profile_type: ProfileType, *, include_inactive: bool = False) -> list[ProcessingProfile]:
        statement = select(ProcessingProfile).where(ProcessingProfile.profile_type == profile_type)
        if not include_inactive:
            statement = statement.where(ProcessingProfile.status == "active")
        result = await self.session.execute(statement.order_by(ProcessingProfile.created_at, ProcessingProfile.name))
        return list(result.scalars())

    async def get(self, profile_type: ProfileType, profile_id: str) -> ProcessingProfile | None:
        result = await self.session.execute(
            select(ProcessingProfile).where(
                ProcessingProfile.id == profile_id,
                ProcessingProfile.profile_type == profile_type,
            )
        )
        return result.scalar_one_or_none()

    async def resolve_filter(self, profile_id: str) -> tuple[FilterProfile, dict[str, object]]:
        row = await self.get("filter", profile_id)
        if row is None or row.status != "active":
            raise ProfileNotFoundError(f"筛选标准不存在或已停用: {profile_id}")
        profile = FilterProfile.model_validate(row.config_json)
        return profile, self._snapshot(row)

    async def resolve_global_filter(self) -> tuple[FilterProfile, dict[str, object]]:
        rows = await self.list("filter")
        if len(rows) != 1:
            raise ProfileNotFoundError("必须且只能启用一套全局过滤标准")
        row = rows[0]
        return FilterProfile.model_validate(row.config_json), self._snapshot(row)

    async def resolve_beautify(self, profile_id: str) -> tuple[BeautifyProfile, dict[str, object]]:
        row = await self.get("beautify", profile_id)
        if row is None or row.status != "active":
            raise ProfileNotFoundError(f"美化标准不存在或已停用: {profile_id}")
        profile = BeautifyProfile.model_validate(row.config_json)
        return profile, self._snapshot(row)

    async def resolve_redaction(
        self, profile_id: str | None = None
    ) -> tuple[RedactionProfile, dict[str, object]]:
        if profile_id:
            row = await self.get("redaction", profile_id)
            if row is None or row.status != "active":
                raise ProfileNotFoundError(f"水印与Logo标准不存在或已停用: {profile_id}")
        else:
            rows = await self.list("redaction")
            if len(rows) != 1:
                raise ProfileNotFoundError("请选择一套水印与Logo标准")
            row = rows[0]
        return RedactionProfile.model_validate(row.config_json), self._snapshot(row)

    async def resolve_completion(
        self, profile_id: str
    ) -> tuple[CompletionProfile, dict[str, object]]:
        row = await self.get("completion", profile_id)
        if row is None or row.status != "active":
            raise ProfileNotFoundError(f"完工分类标准不存在或已停用: {profile_id}")
        profile = CompletionProfile.model_validate(row.config_json)
        return profile, self._snapshot(row)

    async def resolve_routing_profiles(
        self,
        *,
        completion_profile_id: str,
        completed_filter_profile_id: str,
        non_completed_filter_profile_id: str,
    ) -> tuple[
        tuple[CompletionProfile, dict[str, object]],
        tuple[ProcessingStandard, dict[str, object]],
        tuple[ProcessingStandard, dict[str, object]],
    ]:
        completion = await self.resolve_completion(completion_profile_id)
        branches: list[tuple[ProcessingStandard, dict[str, object]]] = []
        for profile_id in (completed_filter_profile_id, non_completed_filter_profile_id):
            row = await self.get("standard", profile_id)
            if row is None or row.status != "active":
                raise ProfileNotFoundError(f"分支过滤标准不存在或已停用: {profile_id}")
            branches.append(
                (ProcessingStandard.model_validate(row.config_json), self._snapshot(row))
            )
        if completed_filter_profile_id == non_completed_filter_profile_id:
            raise ProfileNotFoundError("完工与非完工过滤标准不能相同")
        return completion, branches[0], branches[1]

    async def resolve_standards(
        self,
        profile_ids: list[str] | None = None,
        *,
        require_fallback: bool = False,
    ) -> list[tuple[ProcessingStandard, dict[str, object]]]:
        if profile_ids:
            if not 1 <= len(profile_ids) <= 20:
                raise ProfileNotFoundError("每个任务支持 1 至 20 套分类过滤标准")
            if len(set(profile_ids)) != len(profile_ids):
                raise ProfileNotFoundError("过滤标准不能重复")
            rows: list[ProcessingProfile] = []
            for profile_id in profile_ids:
                row = await self.get("standard", profile_id)
                if row is None or row.status != "active":
                    raise ProfileNotFoundError(f"过滤标准不存在或已停用: {profile_id}")
                rows.append(row)
        else:
            rows = await self.list("standard")
        if not 1 <= len(rows) <= 20:
            raise ProfileNotFoundError("请先配置 1 至 20 套启用中的分类过滤标准")
        resolved = [
            (ProcessingStandard.model_validate(row.config_json), self._snapshot(row))
            for row in rows
        ]
        if require_fallback:
            fallback_count = sum(profile.is_fallback for profile, _ in resolved)
            if fallback_count != 1:
                raise ProfileNotFoundError("启用中的过滤标准必须且只能设置一条兜底分类")
        return sorted(resolved, key=lambda entry: (-entry[0].priority, entry[0].id))

    async def create(
        self,
        profile_type: ProfileType,
        *,
        name: str,
        instruction: str,
        description: str,
        config: dict[str, object],
    ) -> ProcessingProfile:
        prefix = {
            "filter": "flt",
            "beautify": "bty",
            "redaction": "rdc",
            "standard": "std",
            "completion": "cmp",
        }[profile_type]
        profile_id = f"{prefix}_{uuid4().hex}"
        candidate = {**config, "id": profile_id, "version": 1, "description": description.strip()}
        validated = self._validate(profile_type, candidate)
        if profile_type == "filter" and await self.list("filter"):
            raise ManagedProfileError("只能启用一套全局过滤标准，请直接编辑现有标准")
        if profile_type == "standard" and validated.is_fallback:
            await self._ensure_single_active_fallback()
        row = ProcessingProfile(
            id=profile_id,
            profile_type=profile_type,
            name=name.strip(),
            instruction=instruction.strip(),
            description=description.strip(),
            config_json=validated.model_dump(mode="json"),
            version=1,
            status="active",
        )
        self.session.add(row)
        try:
            await self.session.commit()
        except Exception as exc:
            await self.session.rollback()
            raise ManagedProfileError("同类型标准名称不能重复") from exc
        await self.session.refresh(row)
        return row

    async def update(
        self,
        profile_type: ProfileType,
        profile_id: str,
        *,
        expected_version: int,
        name: str,
        instruction: str,
        description: str,
        config: dict[str, object],
    ) -> ProcessingProfile:
        row = await self.get(profile_type, profile_id)
        if row is None or row.status != "active":
            raise ManagedProfileError("标准不存在或已停用")
        if row.version != expected_version:
            raise ManagedProfileError("标准已被其他操作修改，请刷新后重试")
        candidate = {
            **config,
            "id": profile_id,
            "version": row.version + 1,
            "description": description.strip(),
        }
        validated = self._validate(profile_type, candidate)
        if profile_type == "standard" and validated.is_fallback:
            await self._ensure_single_active_fallback(exclude_profile_id=profile_id)
        row.name = name.strip()
        row.instruction = instruction.strip()
        row.description = description.strip()
        row.config_json = validated.model_dump(mode="json")
        row.version += 1
        try:
            await self.session.commit()
        except Exception as exc:
            await self.session.rollback()
            raise ManagedProfileError("同类型标准名称不能重复") from exc
        await self.session.refresh(row)
        return row

    async def archive(self, profile_type: ProfileType, profile_id: str) -> None:
        row = await self.get(profile_type, profile_id)
        if row is None:
            raise ManagedProfileError("标准不存在")
        if profile_type == "filter":
            raise ManagedProfileError("全局过滤标准不能停用，请直接编辑规则")
        row.status = "inactive"
        await self.session.commit()

    async def compile(self, profile_type: ProfileType, instruction: str) -> CompiledProfile:
        if profile_type == "standard":
            raise ManagedProfileError("过滤标准需要分别填写分类标准和过滤规则")
        if profile_type == "completion":
            description = instruction.strip()[:500]
            profile = CompletionProfile(
                id="preview", version=1, description=description
            )
            return CompiledProfile(
                description, profile.model_dump(mode="json"), []
            )
        if profile_type == "redaction":
            return _compile_redaction_instruction(instruction)
        settings = load_ai_model_settings(self.settings)
        if not settings.ai_tagging_enabled or not settings.ai_tagging_api_key:
            raise ManagedProfileError("请先在 AI 配置中启用模型并填写 API Key")
        base = _neutral_profile(profile_type)
        response = await asyncio.to_thread(
            _compile_request, settings, profile_type, instruction.strip(), base
        )
        parameters = response.get("parameters")
        if not isinstance(parameters, dict):
            raise ManagedProfileError("AI 返回的标准参数格式不正确")
        # Runtime vision AI interprets both standards per image. Saved visual
        # thresholds must never become a second source of business behavior.
        parameters = {}
        merged = _deep_merge(base, parameters)
        merged["id"] = "preview"
        merged["version"] = 1
        description = str(response.get("description") or instruction).strip()[:500]
        merged["description"] = description
        validated = self._validate(profile_type, merged)
        unsupported = [str(value)[:120] for value in response.get("unsupported", []) if str(value).strip()]
        return CompiledProfile(description, validated.model_dump(mode="json"), unsupported)

    def compile_standard(
        self,
        *,
        filter_rule: str,
        priority: int,
        classification_rule: str | None = None,
        activation_rule: str | None = None,
        is_fallback: bool = False,
    ) -> CompiledStandard:
        rule = (classification_rule or activation_rule or "").strip()
        description = (
            f"分类为“{rule}”时，执行与其一一对应的过滤规则。"
        )[:500]
        candidate = ProcessingStandard(
            id="preview",
            version=1,
            description=description,
            classification_rule=rule,
            filter_rule=filter_rule.strip(),
            priority=priority,
            is_fallback=is_fallback,
        )
        return CompiledStandard(
            description=description,
            config=candidate.model_dump(mode="json"),
            unsupported=[],
        )

    @staticmethod
    def _validate(profile_type: ProfileType, config: dict[str, object]):
        try:
            if profile_type == "filter":
                return FilterProfile.model_validate(config)
            if profile_type == "beautify":
                return BeautifyProfile.model_validate(config)
            if profile_type == "completion":
                return CompletionProfile.model_validate(config)
            if profile_type == "redaction":
                return RedactionProfile.model_validate(config)
            return ProcessingStandard.model_validate(config)
        except ValidationError as exc:
            raise ManagedProfileError("标准参数超出允许范围") from exc

    async def _ensure_single_active_fallback(
        self, *, exclude_profile_id: str | None = None
    ) -> None:
        await self.session.execute(
            select(func.pg_advisory_xact_lock(func.hashtext("processing_standard_fallback")))
        )
        for row in await self.list("standard"):
            if row.id == exclude_profile_id:
                continue
            if bool(row.config_json.get("is_fallback")):
                raise ManagedProfileError("启用中的过滤标准只能设置一条兜底分类")

    @staticmethod
    def _snapshot(row: ProcessingProfile) -> dict[str, object]:
        return {
            "id": row.id,
            "name": row.name,
            "version": row.version,
            "instruction": row.instruction,
            "config": row.config_json,
        }


def filter_from_snapshot(snapshot: dict[str, object] | None, fallback_id: str, settings: Settings) -> FilterProfile:
    if snapshot and isinstance(snapshot.get("config"), dict):
        return FilterProfile.model_validate(snapshot["config"])
    raise ProfileNotFoundError(f"任务缺少筛选标准快照: {fallback_id}")


def beautify_from_snapshot(snapshot: dict[str, object] | None, fallback_id: str, settings: Settings) -> BeautifyProfile:
    if snapshot and isinstance(snapshot.get("config"), dict):
        return BeautifyProfile.model_validate(snapshot["config"])
    raise ProfileNotFoundError(f"任务缺少美化标准快照: {fallback_id}")


def redaction_from_snapshot(
    snapshot: dict[str, object] | None,
    *,
    legacy_beautify_snapshot: dict[str, object] | None = None,
) -> RedactionProfile:
    if snapshot and isinstance(snapshot.get("config"), dict):
        return RedactionProfile.model_validate(snapshot["config"])
    legacy = (
        legacy_beautify_snapshot.get("config")
        if isinstance(legacy_beautify_snapshot, dict)
        else None
    )
    if isinstance(legacy, dict):
        return RedactionProfile(
            id="legacy_beautify_redaction",
            version=int(legacy.get("version") or 1),
            description="兼容历史任务中的水印与Logo配置",
            watermark=legacy.get("watermark_removal", {}),
            logo=legacy.get("logo_mosaic", {}),
        )
    return RedactionProfile(
        id="system_redaction_disabled",
        version=1,
        description="未配置水印与Logo处理",
    )


def standards_from_snapshots(
    snapshots: list[dict[str, object]] | None,
) -> list[ProcessingStandard]:
    standards: list[ProcessingStandard] = []
    for snapshot in snapshots or []:
        config = snapshot.get("config")
        if isinstance(config, dict):
            standards.append(
                ProcessingStandard.model_validate(
                    {
                        **config,
                        "name": str(
                            snapshot.get("name") or config.get("description") or ""
                        ),
                    }
                )
            )
    return sorted(standards, key=lambda standard: (-standard.priority, standard.id))


def standard_with_global_filter(
    standard: ProcessingStandard,
    global_snapshot: dict[str, object] | None,
) -> ProcessingStandard:
    global_rule = str((global_snapshot or {}).get("instruction") or "").strip()
    if not global_rule:
        return standard
    return standard.model_copy(
        update={
            "filter_rule": (
                "【全局过滤标准｜优先执行】\n"
                f"{global_rule}\n\n"
                "【当前分类专属过滤标准】\n"
                f"{standard.filter_rule}\n\n"
                "判定顺序：先逐项审核全局过滤标准；全局任意一项不通过时必须 reject。"
                "全局全部通过后，才审核当前分类专属标准；两层全部通过才允许 pass。"
            )
        }
    )


def legacy_standard_from_snapshots(
    filter_snapshot: dict[str, object] | None,
    beautify_snapshot: dict[str, object] | None,
    filter_fallback: str,
    beautify_fallback: str,
) -> ProcessingStandard:
    del beautify_snapshot, beautify_fallback
    filter_config = filter_snapshot.get("config") if filter_snapshot else None
    filter_description = (
        str(filter_config.get("description", "保留有效、可辨认的图片"))
        if isinstance(filter_config, dict)
        else "保留有效、可辨认的图片"
    )
    filter_rule = str((filter_snapshot or {}).get("instruction") or filter_description)
    return ProcessingStandard(
        id="legacy_standard",
        version=1,
        description="历史任务兼容标准",
        classification_rule="始终选中这套历史处理标准",
        filter_rule=filter_rule,
    )


def passthrough_standard() -> ProcessingStandard:
    """System rule used when business filtering is disabled for a job."""
    return ProcessingStandard(
        id="system_passthrough",
        name="不执行条件筛选",
        version=1,
        description="跳过业务条件筛选",
        classification_rule="始终选中",
        filter_rule="保留图片并继续后续处理",
        priority=10000,
    )


def neutral_beautify_snapshot() -> dict[str, object]:
    """Immutable delivery profile used when visual enhancement is disabled."""
    config = {
        **_neutral_profile("beautify"),
        "id": "system_delivery",
        "description": "保持原始画面，仅校正方向并生成标准交付文件",
    }
    return {
        "id": "system_delivery",
        "name": "保持原图",
        "version": 1,
        "instruction": "不要调整画面内容、色彩、明暗或清晰度，只校正 EXIF 方向并规范输出格式",
        "config": config,
    }


def default_redaction_snapshot() -> dict[str, object]:
    config = {
        **_neutral_profile("redaction"),
        "id": "redaction_default_v1",
        "version": 3,
    }
    return {
        "id": "redaction_default_v1",
        "name": "当家水印与Logo标准",
        "version": 3,
        "instruction": (
            "左下角拍摄水印始终允许通过；任务开启水印处理时仅用透明小当图标遮挡英文APP，"
            "其他水印文字和画面保持不变；保留当家文字，仅用小当图标遮挡APP；"
            "当家品牌地膜占比达到75%判定不合格。"
        ),
        "config": config,
    }


def _neutral_profile(profile_type: ProfileType) -> dict[str, object]:
    if profile_type == "filter":
        return {
            "id": "preview",
            "version": 1,
            "description": "由用户处理要求驱动的 AI 过滤标准",
        }
    if profile_type == "completion":
        return {
            "id": "preview",
            "version": 1,
            "description": "逐图判断装修空间是否完工",
        }
    if profile_type == "redaction":
        return {
            "id": "preview",
            "version": 1,
            "description": "左下角拍摄水印放行并按任务开关仅遮挡APP，保留其他文字；保留当家文字且仅遮挡APP，大面积品牌地膜不合格",
            "watermark": {
                "enabled": True,
                "allow_during_filter": True,
                "post_action": "remove",
            },
            "logo": {
                "enabled": True,
                "action": "overlay_asset",
                "target_component": "app_text",
                "overlay_asset_id": "xiaodang_cutout_v1",
            },
            "branded_ground_film": {
                "enabled": True,
                "reject_coverage_gte": 0.75,
                "review_margin": 0.05,
                "min_confidence": 0.70,
            },
        }
    return {
        "id": "preview",
        "version": 1,
        "description": "由用户处理要求驱动的 AI 美化标准",
        "brightness": 1.0,
        "contrast": 1.0,
        "color": 1.0,
        "sharpness": 1.0,
        "auto_white_balance": False,
        "white_balance_strength": 0.0,
        "shadow_lift": 0.0,
        "highlight_recovery": 0.0,
        "denoise_strength": 0.0,
        "local_tone_strength": 0.0,
        "local_tone_clip_limit": 1.5,
        "glare_reduction_strength": 0.0,
        "local_clarity_strength": 0.0,
        "auto_straighten": False,
        "max_straighten_degrees": 3.0,
        "min_output_long_side": 2048,
        "jpeg_quality": 95,
    }


def _deep_merge(base: dict[str, object], updates: dict[str, object]) -> dict[str, object]:
    result = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)  # type: ignore[arg-type]
        else:
            result[key] = value
    return result


def _compile_redaction_instruction(instruction: str) -> CompiledProfile:
    """Compile supported Chinese policy language into deterministic settings."""
    import re

    text = instruction.strip()
    base = _neutral_profile("redaction")
    unsupported: list[str] = []
    percentages = [float(value) for value in re.findall(r"(\d+(?:\.\d+)?)\s*%", text)]
    if percentages:
        threshold = percentages[-1] / 100.0
        if 0.05 <= threshold <= 0.98:
            base["branded_ground_film"]["reject_coverage_gte"] = threshold  # type: ignore[index]
        else:
            unsupported.append("地膜占比阈值必须在5%到98%之间")

    watermark = base["watermark"]  # type: ignore[assignment]
    if "水印" not in text:
        watermark["enabled"] = False
    else:
        watermark["allow_during_filter"] = any(
            marker in text for marker in ("允许通过", "允许筛选", "不作为不合格", "放行")
        )
        if any(marker in text for marker in ("保留水印", "不去除水印", "无需去除")):
            watermark["post_action"] = "keep"
        elif any(marker in text for marker in ("去除", "消除", "移除")):
            watermark["post_action"] = "remove"

    logo = base["logo"]  # type: ignore[assignment]
    if "LOGO" not in text.upper() and "标志" not in text:
        logo["enabled"] = False
    elif "马赛克" in text and "小当" not in text:
        logo["action"] = "mosaic"
    elif any(marker in text for marker in ("小当", "图标遮挡", "图标覆盖")):
        logo["action"] = "overlay_asset"

    ground = base["branded_ground_film"]  # type: ignore[assignment]
    if not any(marker in text for marker in ("地膜", "保护膜")):
        ground["enabled"] = False
    if any(marker in text for marker in ("任意品牌", "所有品牌", "第三方品牌")):
        unsupported.append("任意第三方品牌Logo识别尚未配置训练模型")

    description = "；".join([
        (
            "左下角拍摄水印允许通过筛选"
            if watermark.get("allow_during_filter")
            else "按普通规则审核水印"
        ),
        (
            "通过后去除左下角水印"
            if watermark.get("enabled") and watermark.get("post_action") == "remove"
            else "不执行水印去除"
        ),
        (
            "保留“当家”，仅使用小当图标遮挡APP字样"
            if logo.get("enabled") and logo.get("action") == "overlay_asset"
            else "目标Logo使用马赛克遮挡" if logo.get("enabled") else "不处理Logo"
        ),
        (
            f"当家品牌地膜占比达到{ground.get('reject_coverage_gte', 0.75):.0%}判定不合格"
            if ground.get("enabled")
            else "不执行品牌地膜占比筛选"
        ),
    ])
    validated = RedactionProfile.model_validate({**base, "description": description})
    return CompiledProfile(
        description=description,
        config=validated.model_dump(mode="json"),
        unsupported=unsupported,
    )


def _compile_request(settings: Settings, profile_type: ProfileType, instruction: str, base: dict[str, object]) -> dict[str, object]:
    field_scope = (
        "过滤要求不转换为本地阈值。尺寸、质量指标和图片可见内容均由运行时视觉 AI "
        "结合图片元数据直接判断，因此 parameters 必须返回空对象。"
        "只有依赖图片外部信息且无法从画面或元数据判断的要求才是不支持。"
        if profile_type == "filter"
        else "美化要求由运行时视觉 AI 针对每张图片直接转换为完整参数，因此 parameters 必须返回空对象。"
    )
    prompt = f"""你是图片处理参数配置助手。把用户要求转换成 JSON 参数补丁。
{field_scope}
运行时视觉 AI 能直接执行的自然语言要求即使不产生参数也属于支持能力。只有整个处理流程确实无法实现的要求才放入 unsupported 数组。
description 必须用客户能直接理解的自然语言复述执行规则；不要出现参数名，也不要添加用户没有明确输入的阈值、比例或默认值。
返回格式：{{"description":"不超过100字", "parameters":{{}}, "unsupported":[]}}
基准配置：{json.dumps(base, ensure_ascii=False)}
用户要求：{instruction}"""
    body = {
        "model": settings.ai_tagging_model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "max_completion_tokens": 1600,
        "messages": [{"role": "system", "content": "只输出合法 JSON。"}, {"role": "user", "content": prompt}],
    }
    request = Request(
        f"{settings.ai_tagging_base_url.rstrip('/')}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {settings.ai_tagging_api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=settings.ai_tagging_timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    try:
        return json.loads(payload["choices"][0]["message"]["content"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ManagedProfileError("AI 返回的标准内容无法解析") from exc
