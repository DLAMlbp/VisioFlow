from types import SimpleNamespace

import pytest

from src.services.images.tagging import TagPayload, TaggingOutcome
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
        return SimpleNamespace(cancel_requested_at=None, routing_mode="completion")

    async def upsert_ai_tag(self, **values):
        type(self).saved = values

    async def complete_analysis_stage(self, _image_id: str, *, succeeded: bool):
        assert succeeded is True

    async def claim_match_if_ready(self, _image_id: str):
        return True


class _Storage:
    async def download(self, object_key: str):
        assert object_key == "analysis/enhanced.jpg"
        return b"beautified-image"


async def _analyze_many(_provider, images, _max_retries):
    assert images == [b"beautified-image"]
    return [
        TaggingOutcome(
            status="completed",
            payload=TagPayload(
                summary="美化后的完工厨房",
                scene="住宅室内",
                space="厨房",
                condition="完工",
                objects=["橱柜"],
                tags=["模型不得写入的标签"],
                categories={"模型标签": ["厨房"]},
                candidate_tags=["候选标签"],
                confidence=0.9,
            ),
            duration_ms=800,
        )
    ]


def _item():
    return SimpleNamespace(
        id="img_test",
        job_id="job_test",
        object_key="uploads/source.jpg",
        analysis_object_key="analysis/enhanced.jpg",
        ai_tag=SimpleNamespace(provider="library"),
    )


@pytest.mark.asyncio
async def test_analysis_recognizes_beautified_image_without_writing_model_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _Repository.saved = None
    monkeypatch.setattr(analysis, "AsyncSessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(analysis, "ImageJobRepository", _Repository)
    monkeypatch.setattr(analysis, "get_settings", lambda: object())
    monkeypatch.setattr(
        analysis,
        "load_ai_model_settings",
        lambda _settings: SimpleNamespace(
            ai_tagging_concurrency=4,
            ai_tagging_max_retries=2,
            ai_tagging_provider="openai",
            ai_tagging_model="gpt-5.6-sol",
            ai_tagging_store_raw_response=False,
        ),
    )
    monkeypatch.setattr(analysis, "get_storage_provider", lambda: _Storage())
    monkeypatch.setattr(analysis, "get_tag_provider", lambda _settings: object())
    monkeypatch.setattr(analysis, "analyze_many_with_retries", _analyze_many)
    published: list[str] = []
    monkeypatch.setattr(
        analysis.MatchTaskPublisher,
        "publish",
        lambda _publisher, image_id: published.append(image_id),
    )

    await analysis._analyze_image_content("img_test")

    assert _Repository.saved is not None
    assert _Repository.saved["source_object_key"] == "analysis/enhanced.jpg"
    assert _Repository.saved["tag_json"]["summary"] == "美化后的完工厨房"
    assert _Repository.saved["tag_json"]["tags"] == []
    assert _Repository.saved["tag_json"]["categories"] == {}
    assert _Repository.saved["tag_json"]["candidate_tags"] == []
    assert _Repository.saved["model_name"] == "gpt-5.6-sol"
    assert _Repository.saved["duration_ms"] == 800
    assert published == ["img_test"]
