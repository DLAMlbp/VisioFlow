from io import BytesIO
from urllib.error import HTTPError

import pytest
from PIL import Image
from pydantic import ValidationError

from src.core.config import Settings
from src.services.images.tagging import (
    DisabledTagProvider,
    OpenAIChatVisionTagProvider,
    TagPayload,
    _chat_completions_url,
    _resize_for_tagging,
    _safe_error_message,
    get_tag_provider,
)


def make_image(width: int = 1600, height: int = 900) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    output = BytesIO()
    image.save(output, format="JPEG")
    return output.getvalue()


def test_tagging_image_is_resized_before_external_request() -> None:
    with Image.open(BytesIO(_resize_for_tagging(make_image(), 1024))) as image:
        assert image.size == (1024, 576)


def test_tag_payload_limits_tags_and_confidence() -> None:
    payload = TagPayload.model_validate(
        {"summary": "客厅施工", "tags": ["装修"], "confidence": 0.8}
    )
    assert payload.tags == ["装修"]

    with pytest.raises(ValidationError):
        TagPayload.model_validate({"tags": [str(index) for index in range(9)], "confidence": 0.8})


@pytest.mark.asyncio
async def test_disabled_tag_provider_preserves_image_with_reason() -> None:
    outcome = await DisabledTagProvider().tag(make_image())
    assert outcome.status == "failed"
    assert outcome.error_message == "AI 标签功能未启用"


def test_openai_provider_is_configurable_without_importing_sdk() -> None:
    provider = get_tag_provider(Settings(ai_tagging_enabled=True, ai_tagging_provider="openai"))
    assert isinstance(provider, OpenAIChatVisionTagProvider)


def test_chat_completions_url_uses_configured_openai_compatible_base_url() -> None:
    assert _chat_completions_url("https://router.keenlight.ai/v1/") == (
        "https://router.keenlight.ai/v1/chat/completions"
    )


def test_tagging_reports_provider_balance_error() -> None:
    error = HTTPError(
        url="https://provider.example/v1/chat/completions",
        code=403,
        msg="Forbidden",
        hdrs=None,
        fp=BytesIO(b'{"code":"INSUFFICIENT_BALANCE","message":"Insufficient account balance"}'),
    )

    assert _safe_error_message(error) == "AI 服务余额不足，请充值后重试"
