from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
from dataclasses import dataclass

from redis import Redis
from sqlalchemy import select

from src.core.config import get_settings
from src.db.session import AsyncSessionLocal
from src.models.image_item import ImageItem
from src.models.image_job import ImageJob
from src.services.jobs.dispatch import recovery_lease_key

TARGET_TASK = "image.classify_completion"
PUBLISHER_NAME = "CompletionTaskPublisher"


@dataclass
class QueueStats:
    total: int = 0
    kept_target: int = 0
    kept_other: int = 0
    dropped_duplicate: int = 0
    dropped_inactive: int = 0
    kept_unparseable: int = 0

    @property
    def kept(self) -> int:
        return self.kept_target + self.kept_other + self.kept_unparseable

    @property
    def dropped(self) -> int:
        return self.dropped_duplicate + self.dropped_inactive


async def pending_image_ids() -> set[str]:
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            select(ImageItem.id)
            .join(ImageJob, ImageJob.id == ImageItem.job_id)
            .where(
                ImageJob.cancel_requested_at.is_(None),
                ImageJob.status.in_(("queued", "processing", "ranking", "analyzing")),
                ImageItem.status == "analyzing",
                ImageItem.completion_status == "pending",
            )
        )
        return set(rows.scalars())


def parse_task(raw: bytes) -> tuple[str, str] | None:
    try:
        envelope = json.loads(raw)
        task_name = str(envelope["headers"]["task"])
        body = envelope["body"]
        if envelope.get("body_encoding") == "base64" or envelope.get("properties", {}).get(
            "body_encoding"
        ) == "base64":
            body = base64.b64decode(body)
        payload = json.loads(body)
        entity_id = str(payload[0][0])
        return task_name, entity_id
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        return None


def should_keep(
    raw: bytes,
    *,
    pending: set[str],
    seen: set[str],
    stats: QueueStats,
) -> tuple[bool, str | None]:
    stats.total += 1
    parsed = parse_task(raw)
    if parsed is None:
        stats.kept_unparseable += 1
        return True, None
    task_name, entity_id = parsed
    if task_name != TARGET_TASK:
        stats.kept_other += 1
        return True, None
    if entity_id not in pending:
        stats.dropped_inactive += 1
        return False, None
    if entity_id in seen:
        stats.dropped_duplicate += 1
        return False, None
    seen.add(entity_id)
    stats.kept_target += 1
    return True, entity_id


def queue_keys(redis: Redis, queue: str) -> list[bytes]:
    prefix = queue.encode()
    return sorted(
        key
        for key in redis.scan_iter(match=prefix + b"*")
        if redis.type(key) == b"list"
    )


def dry_run(redis: Redis, keys: list[bytes], pending: set[str]) -> QueueStats:
    stats = QueueStats()
    seen: set[str] = set()
    chunk_size = 500
    for key in keys:
        end = redis.llen(key) - 1
        while end >= 0:
            start = max(0, end - chunk_size + 1)
            for raw in reversed(redis.lrange(key, start, end)):
                should_keep(raw, pending=pending, seen=seen, stats=stats)
            end = start - 1
    return stats


def apply_dedupe(
    redis: Redis,
    keys: list[bytes],
    pending: set[str],
    *,
    expected_total: int,
) -> QueueStats:
    actual_total = sum(redis.llen(key) for key in keys)
    if actual_total != expected_total:
        raise RuntimeError(
            f"queue changed after dry-run: expected {expected_total}, found {actual_total}"
        )

    stats = QueueStats()
    seen: set[str] = set()
    lease_seconds = get_settings().pipeline_recovery_lease_seconds
    for key in keys:
        digest = hashlib.sha256(key).hexdigest()[:16].encode()
        backup = b"image-intelligence:dedupe:backup:" + digest
        temporary = b"image-intelligence:dedupe:result:" + digest
        if redis.exists(backup) or redis.exists(temporary):
            raise RuntimeError(f"unfinished dedupe state exists for queue key {digest.decode()}")

        redis.rename(key, backup)
        while True:
            raw = redis.lindex(backup, -1)
            if raw is None:
                break
            keep, entity_id = should_keep(raw, pending=pending, seen=seen, stats=stats)
            transaction = redis.pipeline(transaction=True)
            transaction.rpop(backup)
            if keep:
                transaction.lpush(temporary, raw)
            transaction.execute()
            if entity_id is not None:
                redis.set(
                    recovery_lease_key(PUBLISHER_NAME, entity_id),
                    "deduplicated",
                    ex=lease_seconds,
                )

        if redis.exists(temporary):
            redis.rename(temporary, key)
    return stats


def print_stats(mode: str, stats: QueueStats, pending_count: int, key_count: int) -> None:
    print(
        f"mode={mode} keys={key_count} pending_images={pending_count} total={stats.total} "
        f"kept={stats.kept} kept_target={stats.kept_target} kept_other={stats.kept_other} "
        f"kept_unparseable={stats.kept_unparseable} dropped={stats.dropped} "
        f"dropped_duplicate={stats.dropped_duplicate} dropped_inactive={stats.dropped_inactive}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Safely deduplicate a stopped Celery queue.")
    parser.add_argument("--queue", default="classification")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-total", type=int)
    args = parser.parse_args()

    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    pending = asyncio.run(pending_image_ids())
    keys = queue_keys(redis, args.queue)
    if not keys:
        raise RuntimeError(f"no Redis list keys found for queue {args.queue}")

    if not args.apply:
        stats = dry_run(redis, keys, pending)
        print_stats("dry-run", stats, len(pending), len(keys))
        return
    if args.expected_total is None:
        raise RuntimeError("--expected-total is required with --apply")

    stats = apply_dedupe(redis, keys, pending, expected_total=args.expected_total)
    print_stats("apply", stats, len(pending), len(keys))


if __name__ == "__main__":
    main()
