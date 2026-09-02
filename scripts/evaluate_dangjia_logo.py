from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from src.services.images.logo_detector import LogoDetection
from src.services.profiles import LogoMosaicConfig

Box = tuple[float, float, float, float]


class Detector(Protocol):
    model_version: str

    def detect(
        self, image_bgr: np.ndarray, config: LogoMosaicConfig
    ) -> list[LogoDetection]: ...


def _iou(first: Box, second: Box) -> float:
    x0, y0 = max(first[0], second[0]), max(first[1], second[1])
    x1, y1 = min(first[2], second[2]), min(first[3], second[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    first_area = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
    second_area = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
    return intersection / max(1e-9, first_area + second_area - intersection)


def _decode(path: Path) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"图片无法解码：{path}")
    return image


def evaluate_detector(
    detector: Detector,
    dataset: Path,
    *,
    confidence: float = 0.45,
    iou_threshold: float = 0.5,
    max_images: int | None = None,
) -> dict[str, object]:
    annotation_path = dataset / "annotations" / "instances_val.json"
    payload = json.loads(annotation_path.read_text(encoding="utf-8"))
    categories = {
        int(item["id"]): str(item["name"])
        for item in payload["categories"]
    }
    images = list(payload["images"])
    if max_images is not None:
        images = images[:max_images]
    selected_ids = {int(item["id"]) for item in images}
    ground_truth: dict[int, list[tuple[str, Box]]] = defaultdict(list)
    for annotation in payload["annotations"]:
        image_id = int(annotation["image_id"])
        if image_id not in selected_ids:
            continue
        x, y, width, height = (float(value) for value in annotation["bbox"])
        ground_truth[image_id].append(
            (categories[int(annotation["category_id"])], (x, y, x + width, y + height))
        )

    true_positive = 0
    false_positive = 0
    false_negative = 0
    negative_images = 0
    negative_image_false_positives = 0
    per_class = {
        name: {"tp": 0, "fp": 0, "fn": 0}
        for name in ("dangjia_logo", "dangjia_product_logo")
    }
    config = LogoMosaicConfig(
        enabled=True,
        confidence=confidence,
        include_product_logos=True,
    )
    for image_info in images:
        image_id = int(image_info["id"])
        expected = ground_truth.get(image_id, [])
        predictions = detector.detect(
            _decode(dataset / "val2017" / str(image_info["file_name"])),
            config,
        )
        if not expected:
            negative_images += 1
            negative_image_false_positives += len(predictions)
        matched: set[int] = set()
        for prediction in sorted(predictions, key=lambda item: item.confidence, reverse=True):
            predicted_class = (
                "dangjia_product_logo" if prediction.product_logo else "dangjia_logo"
            )
            candidates = [
                (index, _iou(prediction.box, box))
                for index, (class_name, box) in enumerate(expected)
                if index not in matched and class_name == predicted_class
            ]
            best = max(candidates, key=lambda item: item[1], default=None)
            if best is not None and best[1] >= iou_threshold:
                matched.add(best[0])
                true_positive += 1
                per_class[predicted_class]["tp"] += 1
            else:
                false_positive += 1
                per_class[predicted_class]["fp"] += 1
        for index, (class_name, _box) in enumerate(expected):
            if index not in matched:
                false_negative += 1
                per_class[class_name]["fn"] += 1

    precision = true_positive / max(1, true_positive + false_positive)
    recall = true_positive / max(1, true_positive + false_negative)
    return {
        "model_version": detector.model_version,
        "images": len(images),
        "negative_images": negative_images,
        "negative_image_false_positives": negative_image_false_positives,
        "confidence_threshold": confidence,
        "iou_threshold": iou_threshold,
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "per_class": per_class,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Dangjia YOLOX ONNX on COCO val")
    parser.add_argument("dataset", type=Path)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("models/logos/dangjia/v1/manifest.json"),
    )
    parser.add_argument("--confidence", type=float, default=0.45)
    parser.add_argument("--iou", type=float, default=0.5)
    parser.add_argument("--min-precision", type=float, default=0.98)
    parser.add_argument("--min-recall", type=float, default=0.95)
    parser.add_argument("--max-images", type=int)
    parser.add_argument("--report", type=Path, default=Path("artifacts/dangjia-logo-eval.json"))
    args = parser.parse_args()

    from src.services.images.adapters.yolox_onnx import YoloXOnnxLogoDetector

    detector = YoloXOnnxLogoDetector(args.manifest)
    report = evaluate_detector(
        detector,
        args.dataset,
        confidence=args.confidence,
        iou_threshold=args.iou,
        max_images=args.max_images,
    )
    report["accepted"] = (
        float(report["precision"]) >= args.min_precision
        and float(report["recall"]) >= args.min_recall
        and int(report["negative_image_false_positives"]) == 0
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["accepted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
