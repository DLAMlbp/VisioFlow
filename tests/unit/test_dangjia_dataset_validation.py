from __future__ import annotations

import json

import pytest

from scripts.validate_dangjia_dataset import validate_dataset


def _write_split(dataset, split: str, batch: str, content: bytes) -> None:
    image_dir = dataset / ("train2017" if split == "train" else "val2017")
    image_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{split}.jpg"
    (image_dir / filename).write_bytes(content)
    images = [
        {
            "id": 1,
            "file_name": filename,
            "width": 100,
            "height": 80,
            "capture_batch": batch,
        }
    ]
    if split == "val":
        (image_dir / "negative.jpg").write_bytes(content + b"-negative")
        images.append(
            {
                "id": 2,
                "file_name": "negative.jpg",
                "width": 100,
                "height": 80,
                "capture_batch": f"{batch}-negative",
            }
        )
    payload = {
        "categories": [
            {"id": 1, "name": "dangjia_logo"},
            {"id": 2, "name": "dangjia_product_logo"},
        ],
        "images": images,
        "annotations": [
            {"id": 1, "image_id": 1, "category_id": 1, "bbox": [10, 10, 20, 20]},
            {"id": 2, "image_id": 1, "category_id": 2, "bbox": [40, 10, 20, 20]},
        ],
    }
    annotations = dataset / "annotations"
    annotations.mkdir(exist_ok=True)
    (annotations / f"instances_{split}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _dataset(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "dataset-manifest.json").write_text(
        json.dumps(
            {
                "id": "fixture-v1",
                "version": 1,
                "authorized_for_training": True,
                "authorization_record": "test-authorization",
            }
        ),
        encoding="utf-8",
    )
    _write_split(dataset, "train", "batch-a", b"train-image")
    _write_split(dataset, "val", "batch-b", b"val-image")
    return dataset


def test_dataset_validator_enforces_authorization_and_split_contract(tmp_path) -> None:
    report = validate_dataset(
        _dataset(tmp_path), min_instances=4, min_class_instances=2
    )
    assert report["authorized_for_training"] is True
    assert report["total_instances"] == 4


def test_dataset_validator_rejects_capture_batch_leakage(tmp_path) -> None:
    dataset = _dataset(tmp_path)
    payload_path = dataset / "annotations" / "instances_val.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["images"][0]["capture_batch"] = "batch-a"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="capture_batch"):
        validate_dataset(dataset, min_instances=4, min_class_instances=2)
