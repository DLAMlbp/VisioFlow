from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import dataclass
from io import BytesIO
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PIL import Image
from pydantic import BaseModel, Field, ValidationError

from src.core.config import Settings

PROMPT_VERSION = "renovation_auto_generic_v2"


class TagPayload(BaseModel):
    summary: str = Field(default="", max_length=80)
    scene: str = Field(default="", max_length=80)
    space: str = Field(default="", max_length=80)
    condition: str = Field(default="", max_length=80)
    content_type: str = Field(default="", max_length=80)
    subjects: list[str] = Field(default_factory=list, max_length=12)
    view: str = Field(default="", max_length=80)
    tags: list[str] = Field(default_factory=list, max_length=8)
    categories: dict[str, list[str]] = Field(default_factory=dict)
    candidate_tags: list[str] = Field(default_factory=list, max_length=8)
    confidence: float = Field(ge=0, le=1)
    risks: list[str] = Field(default_factory=list, max_length=8)


@dataclass(frozen=True)
class TaggingOutcome:
    status: str
    payload: TagPayload | None = None
    raw_response: dict[str, object] | None = None
    error_message: str | None = None
    duration_ms: int | None = None
    retryable: bool = False


class VisionTagProvider(Protocol):
    async def tag(self, image_bytes: bytes) -> TaggingOutcome: ...


class OpenAIChatVisionTagProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def tag(self, image_bytes: bytes) -> TaggingOutcome:
        if not self.settings.ai_tagging_api_key:
            return TaggingOutcome(status="failed", error_message="未配置 AI_TAGGING_API_KEY")

        started = time.perf_counter()
        try:
            response = await asyncio.to_thread(self._request, image_bytes)
            content = response["choices"][0]["message"]["content"]
            payload = TagPayload.model_validate_json(content)
            return TaggingOutcome(
                status="completed",
                payload=payload,
                raw_response=response,
                duration_ms=round((time.perf_counter() - started) * 1000),
            )
        except (HTTPError, URLError, TimeoutError, KeyError, TypeError, ValueError, ValidationError) as exc:
            return TaggingOutcome(
                status="failed",
                error_message=_safe_error_message(exc),
                duration_ms=round((time.perf_counter() - started) * 1000),
                retryable=_is_retryable_error(exc),
            )

    def _request(self, image_bytes: bytes) -> dict[str, object]:
        image_data = base64.b64encode(_resize_for_tagging(image_bytes, self.settings.ai_tagging_image_long_side)).decode()
        body = {
            "model": self.settings.ai_tagging_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "max_completion_tokens": 700,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _USER_PROMPT},
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
        with urlopen(request, timeout=self.settings.ai_tagging_timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))


class DisabledTagProvider:
    async def tag(self, image_bytes: bytes) -> TaggingOutcome:
        return TaggingOutcome(status="failed", error_message="AI 标签功能未启用")


def get_tag_provider(settings: Settings) -> VisionTagProvider:
    if not settings.ai_tagging_enabled:
        return DisabledTagProvider()
    if settings.ai_tagging_provider == "openai":
        return OpenAIChatVisionTagProvider(settings)
    return DisabledTagProvider()


def _resize_for_tagging(image_bytes: bytes, long_side: int) -> bytes:
    with Image.open(BytesIO(image_bytes)) as image:
        image.load()
        image = image.convert("RGB")
        image.thumbnail((long_side, long_side), Image.Resampling.LANCZOS)
        output = BytesIO()
        image.save(output, format="JPEG", quality=85, optimize=True)
        return output.getvalue()


def _safe_error_message(error: Exception) -> str:
    if isinstance(error, HTTPError):
        provider_code = _http_error_code(error)
        if provider_code == "INSUFFICIENT_BALANCE":
            return "AI 服务余额不足，请充值后重试"
        if error.code in {401, 403}:
            return "AI 服务鉴权或模型权限不足，请检查服务配置"
        if error.code == 429:
            return "AI 服务请求频率受限，系统将自动稍后重试"
        return f"AI 服务请求失败（HTTP {error.code}）"
    if isinstance(error, URLError):
        return "AI 服务连接失败"
    if isinstance(error, TimeoutError):
        return "AI 服务请求超时"
    if isinstance(error, ValidationError):
        return "AI 标签响应格式不合法"
    return "AI 标签生成失败"


def _is_retryable_error(error: Exception) -> bool:
    if isinstance(error, HTTPError):
        return error.code == 429 or error.code >= 500
    return isinstance(error, (URLError, TimeoutError))


def _http_error_code(error: HTTPError) -> str | None:
    try:
        payload = json.loads(error.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, OSError):
        return None
    code = payload.get("code") if isinstance(payload, dict) else None
    return code if isinstance(code, str) else None


def _chat_completions_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/chat/completions"


_SYSTEM_PROMPT = """你是装修图片内容分析助手。只输出 JSON，不要 Markdown 或额外说明。
必须返回 summary、scene、space、condition、content_type、subjects、view、confidence、risks、tags、categories、candidate_tags。
只描述图片中能够观察到的内容，不要根据预设业务标签猜测。
summary 不超过 40 个字；subjects 最多 12 个。
scene 优先使用：住宅室内、住宅室外、商业空间、施工现场、人物工作。
space 优先使用：客厅、餐厅、客餐厅、玄关、厨房、卧室、书房、阳台、卫生间、过道；存在多个空间时选择画面主体。
condition 优先使用：装修前、施工中、装修完成。content_type 优先使用：环境展示、人物工作、材料展示、细节展示、文档截图。
view 优先使用：空间全景、中景、细节特写。
tags、candidate_tags 保留为空数组，categories 保留为空对象。无法判断时使用空字符串并降低 confidence。
risks 可包含人脸、证件、手机号或地址、二维码、聊天截图、纯文字、严重遮挡。"""

_USER_PROMPT = """请分析这张已经自然美化后的装修图片，并按以下 JSON 返回：
{
  "summary":"老旧住宅装修前的厨房",
  "scene":"住宅室内",
  "space":"厨房",
  "condition":"装修前",
  "content_type":"环境展示",
  "subjects":["灶台","墙砖","橱柜"],
  "view":"空间全景",
  "tags":[],
  "categories":{},
  "candidate_tags":[],
  "confidence":0.0,
  "risks":[]
}"""
