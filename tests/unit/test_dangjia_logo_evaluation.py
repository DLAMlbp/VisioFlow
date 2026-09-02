from __future__ import annotations

import json

import cv2
import numpy as np

from scripts.evaluate_dangjia_logo import _iou, evaluate_detector
from src.services.images.logo_detector import LogoDetection


class FixtureDetector:
    model_version = "fixture"

    def detect(self, image_bgr, config):
        del config
        marker = int(image_bgr[0, 0, 0])
        if marker < 50:
            return [LogoDetection((10, 10, 30, 30), 0.99)]
        if marker < 150:
            return [LogoDetection((40, 40, 60, 60), 0.98, product_logo=True)]
        return []


def _write_jpeg(path, marker: int) -> None:
    ok, encoded = cv2.imencode(
        ".jpg", np.full((80, 80, 3), marker, dtype=np.uint8)
    )
    assert ok
    path.write_bytes(encoded.tobytes())


def test_iou_matches_identical_and_disjoint_boxes() -> None:
    assert _iou((1, 2, 5, 7), (1, 2, 5, 7)) == 1
    assert _iou((0, 0, 2, 2), (3, 3, 4, 4)) == 0


def test_evaluator_counts_both_logo_classes_and_negative_images(tmp_path) -> None:
    dataset = tmp_path
    (dataset / "annotations").mkdir()
    (dataset / "val2017").mkdir()
    _write_jpeg(dataset / "val2017" / "brand.jpg", 10)
    _write_jpeg(dataset / "val2017" / "product.jpg", 100)
    _write_jpeg(dataset / "val2017" / "negative.jpg", 220)
    payload = {
        "categories": [
            {"id": 1, "name": "dangjia_logo"},
            {"id": 2, "name": "dangjia_product_logo"},
        ],
        "images": [
            {"id": 1, "file_name": "brand.jpg"},
            {"id": 2, "file_name": "product.jpg"},
            {"id": 3, "file_name": "negative.jpg"},
        ],
        "annotations": [
            {"id": 1, "image_id": 1, "category_id": 1, "bbox": [10, 10, 20, 20]},
            {"id": 2, "image_id": 2, "category_id": 2, "bbox": [40, 40, 20, 20]},
        ],
    }
    (dataset / "annotations" / "instances_val.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    report = evaluate_detector(FixtureDetector(), dataset)

    assert report["precision"] == 1
    assert report["recall"] == 1
    assert report["negative_images"] == 1
    assert report["negative_image_false_positives"] == 0
