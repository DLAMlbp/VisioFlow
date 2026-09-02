from pathlib import Path

import cv2
import numpy as np

from scripts.prelabel_dangjia_logo_dataset import build_prelabels
from src.services.images.logo_detector import LogoDetection


class _Detector:
    model_version = "test-proposal-detector"

    def detect(self, image_bgr, config):
        del config
        if int(image_bgr[0, 0, 0]) < 100:
            return [LogoDetection((5, 6, 25, 20), 0.72)]
        return []


def _write_jpeg(path: Path, value: int) -> None:
    ok, encoded = cv2.imencode(
        ".jpg", np.full((30, 40, 3), value, dtype=np.uint8)
    )
    assert ok
    path.write_bytes(encoded.tobytes())


def test_prelabels_include_pending_boxes_and_negative_candidates(tmp_path: Path) -> None:
    _write_jpeg(tmp_path / "positive.jpg", 20)
    _write_jpeg(tmp_path / "negative.jpg", 180)

    payload = build_prelabels(tmp_path, _Detector(), confidence=0.3)

    assert payload["info"]["status"] == "prelabels_not_training_ready"
    assert len(payload["images"]) == 2
    assert len(payload["annotations"]) == 1
    assert payload["annotations"][0]["bbox"] == [5, 6, 20, 14]
    assert payload["annotations"][0]["review_status"] == "pending"
    assert any(image["negative_candidate"] for image in payload["images"])
