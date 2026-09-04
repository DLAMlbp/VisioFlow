from datetime import UTC, datetime

import numpy as np
import pytest

from src.repositories.jobs import ImageJobRepository
from src.services.images.enhancement_pipeline import (
    decode_mask,
    decode_pipeline_state,
    encode_mask,
    encode_pipeline_state,
    new_pipeline_state,
    pipeline_object_key,
    pipeline_state_object_key,
)
from src.services.profiles import WatermarkRemovalConfig
from src.workers.redaction import _prepare_watermark
from src.workers.render import _scaled_watermark_boxes


def test_pipeline_state_round_trips_between_workers() -> None:
    state = new_pipeline_state()
    state.update(
        {
            "stage": "inpaint",
            "watermark_requires_inpaint": True,
            "watermark_audit": {"status": "detected", "confidence": 0.91},
        }
    )

    assert decode_pipeline_state(encode_pipeline_state(state)) == state


def test_watermark_mask_round_trips_losslessly() -> None:
    mask = np.zeros((80, 120), dtype=np.uint8)
    mask[60:75, 5:45] = 255

    assert np.array_equal(decode_mask(encode_mask(mask)), mask)


def test_pipeline_keys_are_private_and_deterministic() -> None:
    assert pipeline_object_key("job_1", "img_1", "watermark-mask", "png") == (
        "enhancement-work/job_1/img_1/watermark-mask.png"
    )
    assert pipeline_state_object_key("job_1", "img_1") == (
        "enhancement-work/job_1/img_1/state.json"
    )


def test_disabled_job_watermark_switch_skips_detector(monkeypatch) -> None:
    def fail_if_loaded(_settings):
        raise AssertionError("watermark detector must remain unloaded")

    monkeypatch.setattr("src.workers.redaction.load_watermark_processor", fail_if_loaded)
    image = np.zeros((200, 160, 3), dtype=np.uint8)

    preparation = _prepare_watermark(
        image,
        WatermarkRemovalConfig(enabled=True),
        object(),
        enabled=False,
    )

    assert preparation.audit["status"] == "disabled_by_job"
    assert preparation.requires_inpaint is False


def test_enabled_job_watermark_switch_uses_overlay_fast_path(monkeypatch) -> None:
    class Processor:
        profile_version = "test-app-overlay"

        def prepare_app_overlay(self, image, _config):
            from src.services.images.watermark import WatermarkPreparation

            return WatermarkPreparation(
                image_bgr=image.copy(),
                mask=np.zeros(image.shape[:2], dtype=np.uint8),
                audit={
                    "status": "detected",
                    "automatic_boxes": [[4, 160, 40, 185]],
                },
                requires_inpaint=False,
            )

    monkeypatch.setattr(
        "src.workers.redaction.load_watermark_processor", lambda _settings: Processor()
    )
    image = np.zeros((200, 160, 3), dtype=np.uint8)

    preparation = _prepare_watermark(
        image,
        WatermarkRemovalConfig(enabled=True),
        object(),
        enabled=True,
    )

    assert preparation.requires_inpaint is False
    assert preparation.audit["automatic_boxes"] == [[4, 160, 40, 185]]
    assert preparation.audit["profile_version"] == "test-app-overlay"


def test_watermark_boxes_scale_with_delivery_image_dimensions() -> None:
    boxes = _scaled_watermark_boxes(
        {
            "image_size": [800, 1000],
            "automatic_boxes": [[80, 800, 160, 900]],
        },
        width=1600,
        height=2000,
    )

    assert boxes == [(160, 1600, 320, 1800)]


class _RepositoryResult:
    def __init__(self, *, rowcount: int = 0, scalar=None) -> None:
        self.rowcount = rowcount
        self._scalar = scalar

    def scalar_one_or_none(self):
        return self._scalar


@pytest.mark.asyncio
async def test_enhancement_stage_claim_requires_an_idle_matching_cursor() -> None:
    class Session:
        def __init__(self) -> None:
            self.statement = None
            self.rollbacks = 0

        async def execute(self, statement):
            self.statement = statement
            return _RepositoryResult(scalar=None)

        async def rollback(self) -> None:
            self.rollbacks += 1

    session = Session()
    repository = ImageJobRepository(session)  # type: ignore[arg-type]

    claimed = await repository.continue_enhancement("img_1", "inpaint")

    assert claimed is None
    assert session.rollbacks == 1
    assert "enhancement_stage_started_at IS NULL" in str(session.statement)
    assert "inpaint" in session.statement.compile().params.values()


@pytest.mark.asyncio
async def test_enhancement_stage_can_advance_only_once() -> None:
    class Session:
        def __init__(self) -> None:
            self.results = iter((1, 0))
            self.statements = []
            self.commits = 0

        async def execute(self, statement):
            self.statements.append(statement)
            return _RepositoryResult(rowcount=next(self.results))

        async def commit(self) -> None:
            self.commits += 1

    session = Session()
    repository = ImageJobRepository(session)  # type: ignore[arg-type]

    first = await repository.advance_enhancement_stage(
        "img_1", current_stage="inpaint", next_stage="enhance"
    )
    duplicate = await repository.advance_enhancement_stage(
        "img_1", current_stage="inpaint", next_stage="enhance"
    )

    assert first is True
    assert duplicate is False
    assert session.commits == 2
    assert "inpaint" in session.statements[0].compile().params.values()
    assert "enhance" in session.statements[0].compile().params.values()


@pytest.mark.asyncio
async def test_stale_enhancement_claim_has_a_bounded_recovery_counter() -> None:
    started_at = datetime(2026, 9, 3, tzinfo=UTC)

    class Session:
        def __init__(self) -> None:
            self.statement = None
            self.commits = 0

        async def execute(self, statement):
            self.statement = statement
            return _RepositoryResult(scalar=1)

        async def commit(self) -> None:
            self.commits += 1

    session = Session()
    repository = ImageJobRepository(session)  # type: ignore[arg-type]

    attempt = await repository.recover_enhancement_stage(
        "img_render",
        expected_stage="render",
        started_at=started_at,
        max_attempts=2,
    )

    assert attempt == 1
    assert session.commits == 1
    statement = str(session.statement)
    assert "enhancement_recovery_attempts <" in statement
    assert "enhancement_stage_started_at" in statement
    assert "render" in session.statement.compile().params.values()
    assert 2 in session.statement.compile().params.values()


@pytest.mark.asyncio
async def test_exhausted_enhancement_failure_locks_active_item_for_isolated_failure() -> None:
    started_at = datetime(2026, 9, 3, tzinfo=UTC)

    class Session:
        def __init__(self) -> None:
            self.statement = None
            self.commits = 0

        async def execute(self, statement):
            self.statement = statement
            return _RepositoryResult(rowcount=1)

        async def rollback(self) -> None:
            raise AssertionError("a matching exhausted stage must not roll back")

    session = Session()
    repository = ImageJobRepository(session)  # type: ignore[arg-type]

    claimed = await repository.claim_exhausted_enhancement_failure(
        "img_render",
        expected_stage="render",
        started_at=started_at,
        max_attempts=2,
    )

    assert claimed is True
    assert session.commits == 0
    statement = str(session.statement)
    assert "enhancement_recovery_attempts >=" in statement
    assert "enhancement_stage_started_at" in statement
    assert "render" in session.statement.compile().params.values()
    assert "failed" not in session.statement.compile().params.values()
    assert "enhancing" in session.statement.compile().params.values()
