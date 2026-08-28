from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Literal
from urllib.request import Request, urlopen
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings
from src.models.processing_profile import ProcessingProfile
from src.services.ai_model_config import load_ai_model_settings
from src.services.profiles import (
    BeautifyProfile,
    FilterProfile,
    ProfileNotFoundError,
)

ProfileType = Literal["filter", "beautify"]


class ManagedProfileError(Exception):
    pass


@dataclass(frozen=True)
class CompiledProfile:
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

    async def resolve_beautify(self, profile_id: str) -> tuple[BeautifyProfile, dict[str, object]]:
        row = await self.get("beautify", profile_id)
        if row is None or row.status != "active":
            raise ProfileNotFoundError(f"美化标准不存在或已停用: {profile_id}")
        profile = BeautifyProfile.model_validate(row.config_json)
        return profile, self._snapshot(row)

    async def create(
        self,
        profile_type: ProfileType,
        *,
        name: str,
        instruction: str,
        description: str,
        config: dict[str, object],
    ) -> ProcessingProfile:
        prefix = "flt" if profile_type == "filter" else "bty"
        profile_id = f"{prefix}_{uuid4().hex}"
        candidate = {**config, "id": profile_id, "version": 1, "description": description.strip()}
        validated = self._validate(profile_type, candidate)
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
        row.status = "inactive"
        await self.session.commit()

    async def compile(self, profile_type: ProfileType, instruction: str) -> CompiledProfile:
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

    @staticmethod
    def _validate(profile_type: ProfileType, config: dict[str, object]):
        try:
            return FilterProfile.model_validate(config) if profile_type == "filter" else BeautifyProfile.model_validate(config)
        except ValidationError as exc:
            raise ManagedProfileError("标准参数超出允许范围") from exc

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


def _neutral_profile(profile_type: ProfileType) -> dict[str, object]:
    if profile_type == "filter":
        return {
            "id": "preview",
            "version": 1,
            "description": "由用户处理要求驱动的 AI 过滤标准",
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
