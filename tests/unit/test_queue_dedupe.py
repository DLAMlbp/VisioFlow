import base64
import json

from scripts.dedupe_celery_queue import QueueStats, parse_task, should_keep


def celery_message(task_name: str, entity_id: str) -> bytes:
    body = json.dumps([[entity_id], {}, {"callbacks": None}]).encode()
    return json.dumps(
        {
            "headers": {"task": task_name},
            "body": base64.b64encode(body).decode(),
            "properties": {"body_encoding": "base64"},
        }
    ).encode()


def test_parse_task_reads_celery_base64_envelope() -> None:
    assert parse_task(celery_message("image.classify_completion", "img_1")) == (
        "image.classify_completion",
        "img_1",
    )


def test_dedupe_keeps_one_pending_message_without_requeueing() -> None:
    stats = QueueStats()
    pending = {"img_1"}
    seen: set[str] = set()
    messages = [
        celery_message("image.classify_completion", "img_1"),
        celery_message("image.classify_completion", "img_1"),
        celery_message("image.classify_completion", "finished"),
        celery_message("image.apply_routed_processing", "img_other"),
        b"not-json",
    ]

    outcomes = [
        should_keep(raw, pending=pending, seen=seen, stats=stats)[0] for raw in messages
    ]

    assert outcomes == [True, False, False, True, True]
    assert stats.total == 5
    assert stats.kept_target == 1
    assert stats.dropped_duplicate == 1
    assert stats.dropped_inactive == 1
    assert stats.kept_other == 1
    assert stats.kept_unparseable == 1
