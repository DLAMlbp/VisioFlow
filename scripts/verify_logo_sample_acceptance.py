from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.core.config import Settings
from src.services.images.adapters.rapidocr import detect_text_lines
from src.services.images.logo_detector import load_logo_detector
from src.services.images.mosaic import Box
from src.services.images.redaction import ImageRedactionService, decode_image, encode_jpeg
from src.services.images.watermark import load_watermark_processor
from src.services.profiles import LogoMosaicConfig


def _iou(first: Box, second: Box) -> float:
    x0 = max(first[0], second[0])
    y0 = max(first[1], second[1])
    x1 = min(first[2], second[2])
    y1 = min(first[3], second[3])
    intersection = max(0, x1 - x0) * max(0, y1 - y0)
    first_area = max(0, first[2] - first[0]) * max(0, first[3] - first[1])
    second_area = max(0, second[2] - second[0]) * max(0, second[3] - second[1])
    return intersection / max(1, first_area + second_area - intersection)


def _match_boxes(
    expected: list[Box], predicted: list[Box], threshold: float
) -> tuple[int, int, int, list[float]]:
    candidates = sorted(
        (
            (_iou(expected_box, predicted_box), expected_index, predicted_index)
            for expected_index, expected_box in enumerate(expected)
            for predicted_index, predicted_box in enumerate(predicted)
        ),
        reverse=True,
    )
    expected_used: set[int] = set()
    predicted_used: set[int] = set()
    matched_ious: list[float] = []
    for overlap, expected_index, predicted_index in candidates:
        if overlap < threshold:
            break
        if expected_index in expected_used or predicted_index in predicted_used:
            continue
        expected_used.add(expected_index)
        predicted_used.add(predicted_index)
        matched_ious.append(overlap)
    true_positives = len(matched_ious)
    return (
        true_positives,
        len(predicted) - true_positives,
        len(expected) - true_positives,
        matched_ious,
    )


def _is_brand_text(text: str) -> bool:
    compact = "".join(text.upper().split())
    return "当家" in compact or "APP" in compact


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify private Dangjia Logo samples")
    parser.add_argument("input_directory", type=Path)
    parser.add_argument("expectations", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--iou", type=float, default=0.5)
    args = parser.parse_args()

    payload = json.loads(args.expectations.read_text(encoding="utf-8"))
    settings = Settings(_env_file=None)
    service = ImageRedactionService(
        watermark_processor=load_watermark_processor(settings),
        logo_detector=load_logo_detector(settings),
    )
    args.output_directory.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []

    for expectation in payload.get("images", []):
        source = args.input_directory / str(expectation["file"])
        image = decode_image(source.read_bytes())
        stage = service.mosaic_logos(image, LogoMosaicConfig(enabled=True))
        predicted = [tuple(box) for box in stage.audit.get("boxes", [])]
        expected = [tuple(box) for box in expectation.get("boxes", [])]
        true_positives, false_positives, false_negatives, matched_ious = (
            _match_boxes(expected, predicted, args.iou)
        )
        post_ocr = detect_text_lines(stage.image_bgr)
        brand_hits = [line.text for line in post_ocr if _is_brand_text(line.text)]
        recognized_text = " ".join(line.text for line in post_ocr)
        missing_preserved_text = [
            token
            for token in expectation.get("preserve_text", [])
            if token not in recognized_text
        ]
        output_path = args.output_directory / f"{source.stem}-verified.jpg"
        output_path.write_bytes(encode_jpeg(stage.image_bgr, quality=95))
        passed = (
            stage.audit.get("status") == "applied"
            and true_positives == len(expected)
            and false_positives == 0
            and false_negatives == 0
            and stage.audit.get("outside_boxes_changed_pixels") == 0
            and not brand_hits
            and not missing_preserved_text
        )
        results.append(
            {
                "file": source.name,
                "passed": passed,
                "expected": len(expected),
                "predicted": len(predicted),
                "true_positives": true_positives,
                "false_positives": false_positives,
                "false_negatives": false_negatives,
                "matched_ious": [round(value, 4) for value in matched_ious],
                "brand_ocr_hits_after_mosaic": brand_hits,
                "missing_preserved_text": missing_preserved_text,
                "audit": stage.audit,
                "output": str(output_path),
            }
        )

    report = {
        "status": "passed"
        if results and all(item["passed"] for item in results)
        else "failed",
        "iou_threshold": args.iou,
        "images": results,
    }
    report_path = args.output_directory / "logo-sample-acceptance.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(report_path)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
