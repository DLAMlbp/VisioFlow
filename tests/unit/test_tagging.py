import json
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


def test_tag_payload_validates_confidence() -> None:
    payload = TagPayload.model_validate(
        {"summary": "客厅施工", "tags": ["装修"], "confidence": 0.8}
    )
    assert payload.tags == ["装修"]

    with pytest.raises(ValidationError):
        TagPayload.model_validate({"tags": ["装修"], "confidence": 1.2})


def test_tag_payload_normalizes_provider_scalar_attributes_and_tag_overflow() -> None:
    payload = TagPayload.model_validate(
        {
            "summary": None,
            "attributes": {
                "orientation": "portrait",
                "aspect_ratio": 0.75,
                "people_count": 0,
            },
            "features": {"lighting": ["natural", 2]},
            "categories": {"场景": "客厅"},
            "tags": [f"标签{index}" for index in range(12)],
        }
    )

    assert payload.summary == ""
    assert payload.attributes == {
        "orientation": ["portrait"],
        "aspect_ratio": ["0.75"],
        "people_count": ["0"],
    }
    assert payload.features == {"lighting": ["natural", "2"]}
    assert payload.categories == {"场景": ["客厅"]}
    assert len(payload.tags) == 8


@pytest.mark.asyncio
async def test_disabled_tag_provider_preserves_image_with_reason() -> None:
    outcome = await DisabledTagProvider().tag(make_image())
    assert outcome.status == "failed"
    assert outcome.error_message == "AI 标签功能未启用"


@pytest.mark.asyncio
async def test_openai_provider_batches_multiple_images_in_one_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIChatVisionTagProvider(Settings(ai_tagging_api_key="test-key"))
    calls = 0

    def fake_request_many(images: list[bytes]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        assert len(images) == 2
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "images": [
                                    {"summary": "厨房", "confidence": 0.9},
                                    {"summary": "客厅", "confidence": 0.8},
                                ]
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(provider, "_request_many", fake_request_many)

    outcomes = await provider.tag_many([make_image(), make_image()])

    assert calls == 1
    assert [outcome.payload.summary for outcome in outcomes if outcome.payload] == ["厨房", "客厅"]


def test_openai_provider_is_configurable_without_importing_sdk() -> None:
    provider = get_tag_provider(Settings(ai_tagging_enabled=True, ai_tagging_provider="openai"))
    assert isinstance(provider, OpenAIChatVisionTagProvider)


def test_ai_tagging_defaults_use_locked_router_and_model() -> None:
    settings = Settings()

    assert settings.ai_tagging_enabled is True
    assert settings.ai_tagging_base_url == "https://router.keenlight.ai/v1"
    assert settings.ai_tagging_model == "gpt-5.6-sol"

    with pytest.raises(ValidationError):
        Settings(ai_tagging_base_url="https://api.example.com/v1")


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
