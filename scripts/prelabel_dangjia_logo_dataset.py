from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.services.images.logo_detector import (
    LogoDetector,
    RapidOcrDangjiaLogoDetector,
)
from src.services.images.redaction import decode_image
from src.services.profiles import LogoMosaicConfig

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def build_prelabels(
    input_directory: Path,
    detector: LogoDetector,
    *,
    confidence: float,
    capture_batch: str | None = None,
) -> dict[str, Any]:
    images: list[dict[str, Any]] = []
    annotations: list[dict[str, Any]] = []
    paths = sorted(
        path
        for path in input_directory.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )
    config = LogoMosaicConfig(enabled=True, confidence=confidence)
    annotation_id = 1
    for image_id, path in enumerate(paths, start=1):
        image = decode_image(path.read_bytes())
        height, width = image.shape[:2]
        relative = path.relative_to(input_directory)
        batch = capture_batch or (
            relative.parts[0] if len(relative.parts) > 1 else input_directory.name
        )
        detections = detector.detect(image, config)
        images.append(
            {
                "id": image_id,
                "file_name": relative.as_posix(),
                "width": width,
                "height": height,
                "capture_batch": batch,
                "negative_candidate": not detections,
                "review_status": "pending",
            }
        )
        for detection in detections:
            x0, y0, x1, y1 = detection.box
            x0, x1 = sorted((max(0, x0), min(width, x1)))
            y0, y1 = sorted((max(0, y0), min(height, y1)))
            box_width = x1 - x0
            box_height = y1 - y0
            if box_width <= 0 or box_height <= 0:
                continue
            annotations.append(
                {
                    "id": annotation_id,
                    "image_id": image_id,
                    "category_id": 1,
                    "bbox": [x0, y0, box_width, box_height],
                    "area": box_width * box_height,
                    "iscrowd": 0,
                    "score": round(detection.confidence, 6),
                    "review_status": "pending",
                    "proposal_source": detector.model_version,
                }
            )
            annotation_id += 1
    return {
        "info": {
            "status": "prelabels_not_training_ready",
            "instructions": (
                "Human-review every box and negative image before copying approved "
                "records into instances_train.json or instances_val.json."
            ),
        },
        "images": images,
        "annotations": annotations,
        "categories": [
            {"id": 1, "name": "dangjia_logo"},
            {"id": 2, "name": "dangjia_product_logo"},
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate human-review-only COCO proposals with RapidOCR"
    )
    parser.add_argument("input_directory", type=Path)
    parser.add_argument("output_json", type=Path)
    parser.add_argument("--confidence", type=float, default=0.30)
    parser.add_argument("--capture-batch")
    args = parser.parse_args()
    if not 0.05 <= args.confidence <= 0.99:
        parser.error("--confidence must be between 0.05 and 0.99")
    if not args.input_directory.is_dir():
        parser.error("input_directory must exist")

    payload = build_prelabels(
        args.input_directory,
        RapidOcrDangjiaLogoDetector(),
        confidence=args.confidence,
        capture_batch=args.capture_batch,
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(args.output_json),
                "images": len(payload["images"]),
                "proposals": len(payload["annotations"]),
                "status": payload["info"]["status"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
