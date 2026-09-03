from __future__ import annotations

import argparse
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from time import perf_counter

from src.core.config import Settings
from src.services.images.logo_detector import LogoDetection, LogoDetector, load_logo_detector
from src.services.images.redaction import ImageRedactionService, decode_image, encode_jpeg
from src.services.images.watermark import load_watermark_processor
from src.services.profiles import LogoMosaicConfig, WatermarkRemovalConfig


class PeakRssSampler:
    def __init__(self) -> None:
        try:
            import psutil
        except ImportError:
            raise RuntimeError("benchmarking requires psutil from the dev extra") from None
        self._process = psutil.Process()
        self.before = int(self._process.memory_info().rss)
        self.peak = self.before
        self.after = self.before
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self) -> None:
        while not self._stop.wait(0.02):
            self.peak = max(self.peak, int(self._process.memory_info().rss))

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_args) -> None:
        self.after = int(self._process.memory_info().rss)
        self.peak = max(self.peak, self.after)
        self._stop.set()
        self._thread.join(timeout=1)


class ManualLogoDetector:
    model_version = "manual-benchmark-boxes"

    def __init__(self, boxes: list[tuple[int, int, int, int]]) -> None:
        self.boxes = boxes

    def detect(self, image_bgr, config):
        del image_bgr, config
        return [LogoDetection(box, 1.0) for box in self.boxes]


def _box(value: str) -> tuple[int, int, int, int]:
    try:
        values = tuple(int(item.strip()) for item in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("box must be x0,y0,x1,y1") from exc
    if len(values) != 4:
        raise argparse.ArgumentTypeError("box must be x0,y0,x1,y1")
    return values


def _inputs(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    suffixes = {".jpg", ".jpeg", ".png", ".webp"}
    return sorted(item for item in path.iterdir() if item.suffix.lower() in suffixes)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark protected redaction stages")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--watermark", action="store_true")
    parser.add_argument("--logo", action="store_true")
    parser.add_argument(
        "--logo-action",
        choices=("mosaic", "overlay_asset"),
        default="mosaic",
    )
    parser.add_argument(
        "--logo-target-component",
        choices=("full_logo", "app_text"),
        default="app_text",
    )
    parser.add_argument("--logo-box", action="append", type=_box, default=[])
    parser.add_argument("--watermark-threshold", type=float, default=0.38)
    parser.add_argument("--concurrency", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    settings = Settings(_env_file=None)
    logo_detector: LogoDetector = (
        ManualLogoDetector(args.logo_box)
        if args.logo_box
        else load_logo_detector(settings)
    )
    service = ImageRedactionService(
        watermark_processor=load_watermark_processor(settings),
        logo_detector=logo_detector,
    )
    sources = _inputs(args.input)
    summary: list[dict[str, object]] = []

    def process(source: Path) -> dict[str, object]:
        started = perf_counter()
        try:
            image = decode_image(source.read_bytes())
            watermark = service.remove_watermark(
                image,
                WatermarkRemovalConfig(
                    enabled=args.watermark,
                    detection_threshold=args.watermark_threshold,
                ),
            )
            logos = service.mosaic_logos(
                watermark.image_bgr,
                LogoMosaicConfig(
                    enabled=args.logo or bool(args.logo_box),
                    action=args.logo_action,
                    target_component=args.logo_target_component,
                ),
            )
            output_path = args.output / f"{source.stem}-redacted.jpg"
            output_path.write_bytes(encode_jpeg(logos.image_bgr, quality=95))
            return {
                "input": str(source),
                "output": str(output_path),
                "width": int(image.shape[1]),
                "height": int(image.shape[0]),
                "duration_ms": round((perf_counter() - started) * 1000),
                "watermark": watermark.audit,
                "logos": logos.audit,
            }
        except Exception as exc:  # noqa: BLE001 - benchmark must continue the batch
            return {
                "input": str(source),
                "status": "failed",
                "duration_ms": round((perf_counter() - started) * 1000),
                "error": str(exc)[:500],
            }

    wall_started = perf_counter()
    with PeakRssSampler() as memory:
        if args.concurrency == 1:
            summary = [process(source) for source in sources]
        else:
            by_source: dict[Path, dict[str, object]] = {}
            with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
                futures = {executor.submit(process, source): source for source in sources}
                for future in as_completed(futures):
                    by_source[futures[future]] = future.result()
            summary = [by_source[source] for source in sources]
    wall_duration_ms = round((perf_counter() - wall_started) * 1000)
    report = args.output / "redaction-benchmark.json"
    report.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    successful_durations = [
        float(item["duration_ms"])
        for item in summary
        if item.get("status") != "failed"
    ]
    aggregate = {
        "images": len(summary),
        "succeeded": len(successful_durations),
        "failed": len(summary) - len(successful_durations),
        "concurrency": args.concurrency,
        "wall_duration_ms": wall_duration_ms,
        "throughput_images_per_second": round(
            len(successful_durations) / max(wall_duration_ms / 1000, 0.001), 3
        ),
        "duration_ms": {
            "p50": round(_percentile(successful_durations, 0.50)),
            "p95": round(_percentile(successful_durations, 0.95)),
            "max": round(max(successful_durations, default=0)),
        },
        "memory_mb": {
            "rss_before": round(memory.before / (1024 * 1024), 2),
            "rss_after": round(memory.after / (1024 * 1024), 2),
            "peak_rss": round(memory.peak / (1024 * 1024), 2),
            "peak_rss_delta": round(
                (memory.peak - memory.before) / (1024 * 1024), 2
            ),
        },
    }
    aggregate_report = args.output / "redaction-benchmark-summary.json"
    aggregate_report.write_text(
        json.dumps(aggregate, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(report)
    print(aggregate_report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
