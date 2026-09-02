from __future__ import annotations

import json
import sys

import cv2
import numpy as np

from scripts.benchmark_redaction import _percentile, main


def test_percentile_uses_linear_interpolation() -> None:
    assert _percentile([], 0.95) == 0
    assert _percentile([10, 20, 30, 40], 0.5) == 25
    assert _percentile([10, 20, 30, 40], 0.95) == 38.5


def test_benchmark_continues_after_an_invalid_image(tmp_path, monkeypatch) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    ok, encoded = cv2.imencode(
        ".png", np.full((40, 60, 3), 127, dtype=np.uint8)
    )
    assert ok
    (input_dir / "good.png").write_bytes(encoded.tobytes())
    (input_dir / "broken.jpg").write_bytes(b"not an image")
    monkeypatch.setattr(
        sys,
        "argv",
        ["benchmark_redaction", str(input_dir), str(output_dir)],
    )

    assert main() == 0

    details = json.loads(
        (output_dir / "redaction-benchmark.json").read_text(encoding="utf-8")
    )
    aggregate = json.loads(
        (output_dir / "redaction-benchmark-summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(details) == 2
    assert aggregate["succeeded"] == 1
    assert aggregate["failed"] == 1
    assert aggregate["concurrency"] == 1
