from __future__ import annotations

from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from src.core.config import Settings
from src.services.jobs.service import ImageJobService, InvalidJobRequest


class MemoryStorage:
    def __init__(self, base_bytes: bytes) -> None:
        self.objects = {"redaction-bases/job_1/image_1.jpg": base_bytes}

    async def download(self, object_key: str) -> bytes:
        if object_key not in self.objects:
            raise FileNotFoundError(object_key)
        return self.objects[object_key]

    async def upload(self, object_key: str, data: bytes, content_type: str) -> None:
        assert content_type == "image/jpeg"
        self.objects[object_key] = data


class ManualReviewRepository:
    def __init__(self, item: SimpleNamespace) -> None:
        self.item = item
        self.saved: dict[str, object] | None = None

    async def get_item(self, image_id: str):
        return self.item if image_id == self.item.id else None

    async def update_manual_logo_redaction(self, image_id: str, **values) -> bool:
        assert image_id == self.item.id
        self.saved = values
        return True


def _jpeg_gradient() -> bytes:
    x = np.arange(120, dtype=np.uint8)[None, :]
    y = np.arange(80, dtype=np.uint8)[:, None]
    image = np.dstack(
        (
            np.broadcast_to(x, (80, 120)),
            np.broadcast_to(y, (80, 120)),
            np.full((80, 120), 160, dtype=np.uint8),
        )
    )
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert ok
    return encoded.tobytes()


def _item() -> SimpleNamespace:
    result = SimpleNamespace(
        enhanced_object_key="enhanced/job_1/image_1.jpg",
        enhancement_audit_json={
            "profile_snapshot": {
                "jpeg_quality": 95,
                "logo_mosaic": {"mosaic_block_ratio": 0.1},
            },
            "redaction": {
                "logos": {
                    "enabled": True,
                    "status": "applied",
                    "boxes": [[8, 8, 32, 28]],
                    "manual_review_available": True,
                }
            },
        },
        reasons_json=["自动处理完成"],
    )
    return SimpleNamespace(
        id="image_1",
        job_id="job_1",
        purged_at=None,
        analysis_object_key="analysis/job_1/image_1.jpg",
        result=result,
    )


@pytest.mark.asyncio
async def test_manual_logo_review_rerenders_and_updates_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _item()
    repository = ManualReviewRepository(item)
    storage = MemoryStorage(_jpeg_gradient())
    monkeypatch.setattr(
        "src.services.jobs.service.get_storage_provider", lambda: storage
    )
    service = ImageJobService(repository=repository, settings=Settings())

    response = await service.update_logo_redaction(
        "job_1", "image_1", [(10, 10, 55, 45)]
    )

    assert response.status == "manual_applied"
    assert response.boxes == [(10, 10, 55, 45)]
    assert "enhanced/job_1/image_1.jpg" in storage.objects
    assert "analysis/job_1/image_1.jpg" in storage.objects
    assert repository.saved is not None
    logos = repository.saved["enhancement_audit"]["redaction"]["logos"]
    assert logos["automatic_boxes"] == [[8, 8, 32, 28]]
    assert logos["boxes"] == [[10, 10, 55, 45]]
    assert logos["source"] == "manual_review"
    assert logos["manual_revision"] == 1


@pytest.mark.asyncio
async def test_manual_logo_review_can_restore_base_by_saving_no_boxes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _item()
    repository = ManualReviewRepository(item)
    storage = MemoryStorage(_jpeg_gradient())
    monkeypatch.setattr(
        "src.services.jobs.service.get_storage_provider", lambda: storage
    )
    service = ImageJobService(repository=repository, settings=Settings())

    response = await service.update_logo_redaction("job_1", "image_1", [])

    assert response.status == "manual_cleared"
    assert response.detections == 0
    assert repository.saved is not None
    logos = repository.saved["enhancement_audit"]["redaction"]["logos"]
    assert logos["boxes"] == []


@pytest.mark.asyncio
async def test_manual_logo_review_rejects_boxes_outside_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _item()
    repository = ManualReviewRepository(item)
    storage = MemoryStorage(_jpeg_gradient())
    monkeypatch.setattr(
        "src.services.jobs.service.get_storage_provider", lambda: storage
    )
    service = ImageJobService(repository=repository, settings=Settings())

    with pytest.raises(InvalidJobRequest, match="超出图片边界"):
        await service.update_logo_redaction(
            "job_1", "image_1", [(10, 10, 121, 45)]
        )
