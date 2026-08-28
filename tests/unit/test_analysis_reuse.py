from types import SimpleNamespace

import pytest

from src.core.config import Settings
from src.workers import analysis


class _SessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *_args):
        return None


class _Repository:
    saved: dict[str, object] | None = None

    def __init__(self, _session) -> None:
        pass

    async def claim_analysis_batch(self, _image_id: str, *, limit: int):
        assert limit == 4
        return [_item()]

    async def get_config(self, _job_id: str):
        return SimpleNamespace(cancel_requested_at=None)

    async def upsert_ai_tag(self, **values):
        type(self).saved = values

    async def complete_analysis_stage(self, _image_id: str, *, succeeded: bool):
        assert succeeded is True

    async def claim_match_if_ready(self, _image_id: str):
        return False


def _item():
    return SimpleNamespace(
        id="img_test",
        job_id="job_test",
        object_key="uploads/source.jpg",
        analysis_object_key="analysis/enhanced.jpg",
        ai_processing_status="completed",
        ai_processing_model="gpt-5.6-sol",
        ai_processing_prompt_version="managed_filter_beautify_content_v2",
        ai_processing_json={
            "filter": {"decision": "pass", "reason": "符合要求", "confidence": 0.9},
            "beautify": {
                "needed": False,
                "reason": "无需调整",
                "parameters": {
                    "brightness": 1.0,
                    "contrast": 1.0,
                    "color": 1.0,
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
                },
            },
            "content": {
                "summary": "施工中的厨房",
                "scene": "住宅室内",
                "space": "厨房",
                "condition": "施工中",
                "content_type": "环境展示",
                "subjects": ["墙砖"],
                "view": "空间全景",
                "tags": [],
                "categories": {},
                "candidate_tags": [],
                "confidence": 0.9,
                "risks": [],
            },
        },
        ai_tag=None,
    )


@pytest.mark.asyncio
async def test_analysis_reuses_combined_content_without_second_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _Repository.saved = None
    monkeypatch.setattr(analysis, "AsyncSessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(analysis, "ImageJobRepository", _Repository)
    monkeypatch.setattr(analysis, "get_settings", lambda: Settings(ai_tagging_api_key="key"))
    monkeypatch.setattr(analysis, "load_ai_model_settings", lambda settings: settings)
    monkeypatch.setattr(analysis, "get_storage_provider", lambda: object())
    monkeypatch.setattr(
        analysis,
        "get_tag_provider",
        lambda _settings: pytest.fail("复用内容识别时不应再次调用标签模型"),
    )

    await analysis._analyze_image_content("img_test")

    assert _Repository.saved is not None
    assert _Repository.saved["source_object_key"] == "uploads/source.jpg"
    assert _Repository.saved["tag_json"]["summary"] == "施工中的厨房"
    assert _Repository.saved["duration_ms"] == 0
