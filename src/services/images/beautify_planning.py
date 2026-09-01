from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.core.config import Settings
from src.services.images.beautify_policy import validate_beautify_plan
from src.services.images.tagging import (
    _chat_completions_url,
    _is_retryable_error,
    _resize_for_tagging,
    _safe_error_message,
)
from src.services.images.vision_rate_limit import run_vision_request
from src.services.profiles import BeautifyProfile

BEAUTIFY_PLAN_PROMPT_VERSION = "post_filter_beautify_v1"
logger = logging.getLogger(__name__)


class BeautifyParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brightness: float = Field(default=1.0, ge=0.5, le=1.5)
    contrast: float = Field(default=1.0, ge=0.5, le=1.5)
    color: float = Field(default=1.0, ge=0.5, le=1.5)
    sharpness: float = Field(default=1.0, ge=0.5, le=2.0)
    auto_white_balance: bool = False
    white_balance_strength: float = Field(default=0.0, ge=0, le=1)
    shadow_lift: float = Field(default=0.0, ge=0, le=0.35)
    highlight_recovery: float = Field(default=0.0, ge=0, le=0.35)
    denoise_strength: float = Field(default=0.0, ge=0, le=0.5)
    local_tone_strength: float = Field(default=0.0, ge=0, le=0.5)
    local_tone_clip_limit: float = Field(default=1.5, ge=1, le=3)
    glare_reduction_strength: float = Field(default=0.0, ge=0, le=0.5)
    local_clarity_strength: float = Field(default=0.0, ge=0, le=0.5)
    auto_straighten: bool = False
    max_straighten_degrees: float = Field(default=3.0, gt=0, le=12)


class BeautifyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    needed: bool
    reason: str = Field(min_length=1, max_length=300)
    confidence: float = Field(default=1.0, ge=0, le=1)
    parameters: BeautifyParameters
    parameter_reasons: dict[str, str] = Field(default_factory=dict)
    risk_flags: list[str] = Field(default_factory=list, max_length=20)


class StoredBeautifyPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: BeautifyDecision
    effective_parameters: BeautifyParameters
    corrections: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class BeautifyPlanOutcome:
    status: Literal["completed", "failed"]
    payload: BeautifyDecision | None = None
    raw_response: dict[str, object] | None = None
    error_message: str | None = None
    duration_ms: int | None = None
    retryable: bool = False


class BeautifyPlanningService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def analyze(
        self,
        image_bytes: bytes,
        *,
        instruction: str,
        image_context: dict[str, int | float] | None = None,
    ) -> BeautifyPlanOutcome:
        if not self.settings.ai_tagging_enabled:
            return BeautifyPlanOutcome(status="failed", error_message="AI 图片处理未启用")
        if not self.settings.ai_tagging_api_key:
            return BeautifyPlanOutcome(
                status="failed", error_message="未配置 AI_TAGGING_API_KEY"
            )

        started = time.perf_counter()
        response: dict[str, object] | None = None
        try:
            response = await run_vision_request(
                self.settings,
                operation="beautify_planning",
                request=lambda: self._request(image_bytes, instruction, image_context),
            )
            content = response["choices"][0]["message"]["content"]
            payload = _parse_beautify_content(content)
            return BeautifyPlanOutcome(
                status="completed",
                payload=payload,
                raw_response=response,
                duration_ms=round((time.perf_counter() - started) * 1000),
            )
        except (
            HTTPError,
            URLError,
            TimeoutError,
            KeyError,
            TypeError,
            ValueError,
            ValidationError,
        ) as exc:
            if isinstance(exc, ValidationError):
                logger.warning("beautify_plan_schema_invalid error=%s", exc)
            return BeautifyPlanOutcome(
                status="failed",
                raw_response=response,
                error_message=_safe_error_message(exc).replace("AI 标签", "AI 美化规划"),
                duration_ms=round((time.perf_counter() - started) * 1000),
                retryable=_is_retryable_error(exc),
            )

    def _request(
        self,
        image_bytes: bytes,
        instruction: str,
        image_context: dict[str, int | float] | None,
    ) -> dict[str, object]:
        resized = _resize_for_tagging(
            image_bytes, self.settings.ai_tagging_image_long_side
        )
        image_data = base64.b64encode(resized).decode()
        prompt = _beautify_prompt(instruction, image_context)
        body = {
            "model": self.settings.ai_tagging_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "max_completion_tokens": 1800,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是图片美化参数规划助手，只输出合法 JSON。"
                        "保持真实内容、构图和比例，不得返回标签或内容分析。"
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_data}",
                                "detail": "low",
                            },
                        },
                    ],
                },
            ],
        }
        request = Request(
            _chat_completions_url(self.settings.ai_tagging_base_url),
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.settings.ai_tagging_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(
            request, timeout=self.settings.ai_beautify_timeout_seconds
        ) as response:
            return json.loads(response.read().decode("utf-8"))


def build_stored_plan(
    profile: BeautifyProfile, decision: BeautifyDecision
) -> StoredBeautifyPlan:
    validated = validate_beautify_plan(
        profile,
        needed=decision.needed,
        parameters=decision.parameters.model_dump(),
    )
    return StoredBeautifyPlan(
        decision=decision,
        effective_parameters=BeautifyParameters.model_validate(
            validated.profile.model_dump(
                include=set(BeautifyParameters.model_fields)
            )
        ),
        corrections=list(validated.corrections),
    )


def beautify_plan_from_json(payload: object) -> StoredBeautifyPlan | None:
    if not isinstance(payload, dict):
        return None
    try:
        return StoredBeautifyPlan.model_validate(payload)
    except ValidationError:
        return None


def neutralize_beautify_profile(profile: BeautifyProfile) -> BeautifyProfile:
    return profile.model_copy(
        update={
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
        }
    )


def _parse_beautify_content(content: object) -> BeautifyDecision:
    if not isinstance(content, str):
        raise TypeError("AI 美化规划响应必须是文本")
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return BeautifyDecision.model_validate_json(text)


def _beautify_prompt(
    instruction: str, image_context: dict[str, int | float] | None
) -> str:
    return f"""图片已经通过整批分类和分支过滤。请只规划当前图片的美化参数。

美化标准：{instruction.strip() or "自然美化，保持内容真实"}
图片元数据和本地质量指标：{json.dumps(image_context or {}, ensure_ascii=False)}

禁止裁切、拉伸、扩图、增删物体或改变构图。无法确认需要调整时 needed=false 并返回中性参数。
参数范围：亮度/对比度/色彩 0.5-1.5；锐化 0.5-2；白平衡强度 0-1；阴影和高光 0-0.35；
降噪/局部层次/反光抑制/局部清晰度 0-0.5；局部层次限制 1-3；拉直角度大于 0 且不超过 12。

只返回以下 JSON，禁止返回 content、tags、categories 或 candidate_tags：
{{
  "needed": true,
  "reason": "逐图美化依据",
  "confidence": 0.0,
  "parameters": {{
    "brightness": 1.0,
    "contrast": 1.0,
    "color": 1.0,
    "sharpness": 1.0,
    "auto_white_balance": false,
    "white_balance_strength": 0.0,
    "shadow_lift": 0.0,
    "highlight_recovery": 0.0,
    "denoise_strength": 0.0,
    "local_tone_strength": 0.0,
    "local_tone_clip_limit": 1.5,
    "glare_reduction_strength": 0.0,
    "local_clarity_strength": 0.0,
    "auto_straighten": false,
    "max_straighten_degrees": 3.0
  }},
  "parameter_reasons": {{}},
  "risk_flags": []
}}"""
