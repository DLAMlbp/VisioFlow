import asyncio
from types import SimpleNamespace
from typing import ClassVar

import pytest

from src.workers import control, preprocess


class _BarrierRepository:
    claimed = 0

    async def get_config(self, _job_id: str):
        return SimpleNamespace(id="job_test", cancel_requested_at=None, max_selected=999, total_count=1)

    async def claim_ranking_if_filtering_complete(self, _job_id: str) -> bool:
        type(self).claimed += 1
        return True


@pytest.mark.asyncio
async def test_max_selected_does_not_bypass_batch_filter_barrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[str] = []
    monkeypatch.setattr(
        preprocess,
        "RankingTaskPublisher",
        lambda: SimpleNamespace(publish=lambda job_id: published.append(job_id)),
    )
    _BarrierRepository.claimed = 0

    await preprocess._advance_after_preprocess(
        _BarrierRepository(), SimpleNamespace(job_id="job_test", status="filtered")
    )

    assert _BarrierRepository.claimed == 1
    assert published == ["job_test"]


class _RankRepository:
    queued: ClassVar[list[str]] = []

    def __init__(self, _session) -> None:
        pass

    async def get_config(self, _job_id: str):
        return SimpleNamespace(cancel_requested_at=None, max_selected=10)

    async def list_filtered_items_by_score(self, _job_id: str):
        return [SimpleNamespace(id="img_1"), SimpleNamespace(id="img_2")]

    async def mark_not_selected(self, _item) -> None:
        raise AssertionError("all filtered images should be selected")

    async def finish_ranking(self, _job_id: str) -> None:
        return None

    async def queue_beautify_plan(self, image_id: str) -> bool:
        type(self).queued.append(image_id)
        return True

    async def complete_job_if_finished(self, _job_id: str) -> None:
        return None


class _SessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *_args):
        return None


@pytest.mark.asyncio
async def test_ranking_publishes_beautify_plans_before_enhancement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[str] = []
    _RankRepository.queued = []
    monkeypatch.setattr(control, "AsyncSessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(control, "ImageJobRepository", _RankRepository)
    monkeypatch.setattr(
        control,
        "BeautifyPlanTaskPublisher",
        lambda: SimpleNamespace(publish=lambda image_id: published.append(image_id)),
    )

    await control._rank_job("job_test")

    assert _RankRepository.queued == ["img_1", "img_2"]
    assert published == ["img_1", "img_2"]


@pytest.mark.asyncio
async def test_concurrent_barrier_attempts_publish_ranking_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[str] = []

    class ConcurrentRepository:
        def __init__(self) -> None:
            self.lock = asyncio.Lock()
            self.claimed = False

        async def get_config(self, job_id: str):
            return SimpleNamespace(id=job_id, cancel_requested_at=None)

        async def claim_ranking_if_filtering_complete(self, _job_id: str) -> bool:
            async with self.lock:
                if self.claimed:
                    return False
                self.claimed = True
                return True

    repository = ConcurrentRepository()
    monkeypatch.setattr(
        preprocess,
        "RankingTaskPublisher",
        lambda: SimpleNamespace(publish=lambda job_id: published.append(job_id)),
    )

    await asyncio.gather(*(
        preprocess._advance_after_preprocess(
            repository,
            SimpleNamespace(job_id="job_concurrent", created_at=preprocess.datetime.now(preprocess.UTC)),
        )
        for _ in range(12)
    ))

    assert published == ["job_concurrent"]
