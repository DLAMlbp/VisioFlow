from __future__ import annotations

import logging
from collections.abc import Mapping


def emit_metric(
    logger: logging.Logger,
    name: str,
    value: float = 1,
    labels: Mapping[str, object] | None = None,
) -> None:
    fields = " ".join(
        f"{key}={_safe_label(label_value)}"
        for key, label_value in sorted((labels or {}).items())
        if label_value is not None
    )
    logger.info("metric=%s value=%s%s", name, value, f" {fields}" if fields else "")


def _safe_label(value: object) -> str:
    return str(value).replace("\n", " ").replace("\r", " ").replace(" ", "_")[:200]
