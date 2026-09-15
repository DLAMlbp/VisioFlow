"""Real Redis admission tests; never connect to a production Redis instance."""

from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from redis.exceptions import RedisError

from src.core.config import Settings
from src.services.images import vision_rate_limit as scheduler
from src.services.images.vision_fair_queue import (
    AGE_PRIORITY_MS,
    CANCEL_WAIT_SCRIPT,
    FAIR_ACQUIRE_SCRIPT,
    MAX_WAITERS,
    STAGE_GRANT_SEQUENCE,
    WAITER_LEASE_MS,
)


@pytest_asyncio.fixture
async def harness():
    url = os.getenv("TIME_OPT_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set TIME_OPT_TEST_REDIS_URL to an isolated loopback Redis")
    if urlsplit(url).hostname not in ("127.0.0.1", "localhost", "::1"):
        pytest.fail("Scheduler integration tests require loopback Redis")
    settings = Settings(
        _env_file=None, redis_url=url, ai_fair_scheduler_enabled=True,
        ai_tagging_provider=f"test-{uuid.uuid4().hex}",
        ai_tagging_rate_limit_per_minute=100, ai_tagging_global_concurrency=1,
    )
    redis = Redis.from_url(url, decode_responses=True, socket_timeout=3)
    await redis.ping()
    keys = [scheduler._rate_key(settings), scheduler._inflight_key(settings),
            scheduler._cooldown_key(settings), scheduler._capacity_key(settings),
            *scheduler._fair_keys(settings)]
    try:
        yield redis, settings, keys
    finally:
        # Only this fixture's random namespace; never FLUSHDB or inspect other data.
        for key in keys:
            await redis.expire(key, 1)
        await redis.aclose()


async def acquire(harness, token, stage, *, group="job-a", now=None):
    redis, settings, keys = harness
    return await redis.eval(
        FAIR_ACQUIRE_SCRIPT, len(keys), *keys,
        now if now is not None else int(time.time() * 1000), 60_000,
        settings.ai_tagging_rate_limit_per_minute, settings.ai_tagging_global_concurrency,
        120_000, token, stage, group, WAITER_LEASE_MS, AGE_PRIORITY_MS, MAX_WAITERS,
    )


async def block(harness):
    redis, _, keys = harness
    await redis.zadd(keys[1], {"holder": int(time.time() * 1000) + 120_000})


async def wait_until_queued(redis, key):
    async def check():
        while not await redis.zcard(key):
            await asyncio.sleep(0.01)
    await asyncio.wait_for(check(), timeout=3)


async def test_classification_gets_three_weighted_turns_and_stage_fifo(harness):
    redis, _, keys = harness
    await block(harness)
    queued = [("a1", 1), ("a2", 1), ("a3", 1), ("b1", 2), ("c1", 3)]
    for token, stage in queued:
        assert (await acquire(harness, token, stage))[0] == 0
    await redis.zrem(keys[1], "holder")
    assert STAGE_GRANT_SEQUENCE[:5] == (1, 1, 1, 2, 3)
    for token, stage in queued:
        assert (await acquire(harness, token, stage))[0] == 1
        await redis.zrem(keys[1], token)
    assert await redis.zcard(keys[4]) == 0


async def test_same_stage_rotates_between_jobs_when_both_are_waiting(harness):
    redis, _, keys = harness
    await block(harness)
    await acquire(harness, "a1", 1, group="job-a")
    await acquire(harness, "a2", 1, group="job-a")
    await acquire(harness, "b1", 1, group="job-b")
    await redis.set(keys[11], "job-a")
    await redis.zrem(keys[1], "holder")

    assert (await acquire(harness, "a1", 1, group="job-a"))[0] == 0
    assert (await acquire(harness, "b1", 1, group="job-b"))[0] == 1


async def test_oldest_aged_attempt_overrides_stage_rotation(harness):
    redis, _, keys = harness
    await block(harness)
    await acquire(harness, "old-classification", 1)
    await acquire(harness, "new-plan", 2)
    await redis.set(keys[8], 1)
    await redis.hset(keys[7], "old-classification", int(time.time() * 1000)-AGE_PRIORITY_MS-1)
    await redis.zrem(keys[1], "holder")
    assert (await acquire(harness, "new-plan", 2))[0] == 0
    assert (await acquire(harness, "old-classification", 1))[0] == 1


async def test_dead_waiter_expires_without_blocking_other_stages(harness):
    redis, _, keys = harness
    await block(harness)
    await acquire(harness, "dead", 1)
    await acquire(harness, "live", 2)
    await redis.zadd(keys[5], {"dead": int(time.time() * 1000)-1})
    await redis.zrem(keys[1], "holder")
    assert (await acquire(harness, "live", 2))[0] == 1
    assert await redis.zscore(keys[4], "dead") is None
    assert await redis.hget(keys[6], "dead") is None
    assert await redis.hget(keys[7], "dead") is None
    assert await redis.hget(keys[10], "dead") is None


async def test_expired_inflight_is_reclaimed(harness):
    redis, _, keys = harness
    await redis.zadd(keys[1], {"dead": int(time.time() * 1000)-1})
    assert (await acquire(harness, "live", 2))[0] == 1
    assert await redis.zcard(keys[1]) == 1


async def test_waiter_heartbeat_preserves_arrival_order(harness):
    redis, _, keys = harness
    await block(harness)
    now = int(time.time() * 1000)
    await acquire(harness, "waiting", 1, now=now)
    sequence = await redis.zscore(keys[4], "waiting")
    await acquire(harness, "waiting", 1, now=now+1000)
    assert await redis.zscore(keys[4], "waiting") == sequence
    assert await redis.hget(keys[7], "waiting") == str(now)
    assert await redis.zscore(keys[5], "waiting") == now+1000+WAITER_LEASE_MS


async def test_wait_queue_has_a_bound_without_evicting_live_work(harness):
    redis, _, keys = harness
    now = int(time.time() * 1000)
    await redis.zadd(keys[4], {f"q{i}": i for i in range(MAX_WAITERS)})
    await redis.zadd(keys[5], {f"q{i}": now+WAITER_LEASE_MS for i in range(MAX_WAITERS)})
    assert (await acquire(harness, "overflow", 1))[0] == -1
    assert await redis.zcard(keys[4]) == MAX_WAITERS
    assert await redis.zscore(keys[4], "overflow") is None


async def test_concurrent_acquisition_never_exceeds_global_limit(harness):
    redis, settings, keys = harness
    settings.ai_tagging_global_concurrency = 5
    results = await asyncio.gather(*(acquire(harness, f"t{i}", 1) for i in range(30)))
    assert sum(r[0] == 1 for r in results) == 5
    assert await redis.zcard(keys[1]) == 5
    assert await redis.zcard(keys[0]) == 5


async def test_rpm_cap_remains_shared_after_inflight_release(harness):
    redis, settings, keys = harness
    settings.ai_tagging_rate_limit_per_minute = 2
    for i in range(2):
        assert (await acquire(harness, f"t{i}", 1))[0] == 1
        await redis.zrem(keys[1], f"t{i}")
    result = await acquire(harness, "later", 2)
    assert result[0] == 0 and result[5] == 1 and result[6] == 0
    assert await redis.zcard(keys[0]) == 2


async def test_cooldown_reports_overlapping_causes_and_reduced_capacity(harness):
    redis, settings, keys = harness
    settings.ai_tagging_global_concurrency = 5
    settings.ai_tagging_rate_limit_per_minute = 1
    now = int(time.time() * 1000)
    await block(harness)
    await redis.zadd(keys[0], {"recent": now})
    await redis.set(keys[2], now+10_000)
    await redis.set(keys[3], 1)
    result = await acquire(harness, "waiting", 1, now=now)
    assert result[0] == 0 and result[4:7] == [1, 1, 1]


async def test_eval_retry_is_idempotent_and_cancel_does_not_refund_rpm(harness):
    redis, _, keys = harness
    assert (await acquire(harness, "same", 1))[0] == 1
    assert (await acquire(harness, "same", 1))[0] == 1
    assert await redis.zcard(keys[0]) == 1
    assert await redis.zcard(keys[1]) == 1
    await redis.eval(
        CANCEL_WAIT_SCRIPT,
        6,
        *keys[4:8],
        keys[10],
        keys[1],
        "same",
    )
    assert await redis.zcard(keys[1]) == 0
    assert await redis.zcard(keys[0]) == 1
    assert (await acquire(harness, "same", 1))[0] == -1


async def test_rollback_path_still_obeys_candidate_capacity(harness):
    redis, settings, keys = harness
    assert (await acquire(harness, "candidate", 1))[0] == 1
    result = await redis.eval(scheduler._ACQUIRE_SCRIPT, 4, *keys[:4],
                              int(time.time()*1000), 60_000, 100,
                              settings.ai_tagging_global_concurrency, 120_000, "legacy")
    assert result[0] == 0 and result[6] == 1


async def test_cancelled_waiter_is_removed_before_any_provider_call(harness, monkeypatch):
    redis, settings, keys = harness
    await block(harness)
    called = []
    task = asyncio.create_task(scheduler.run_vision_request(
        settings, operation="content_analysis", request=lambda: called.append(True),
    ))
    await wait_until_queued(redis, keys[4])
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not called
    assert await redis.zcard(keys[4]) == 0
    assert await redis.zcard(keys[5]) == 0
    assert await redis.zcard(keys[1]) == 1  # unrelated holder is preserved


async def test_lost_grant_response_never_calls_provider_or_leaks_lease(harness, monkeypatch):
    redis, settings, keys = harness
    class LostResponse:
        async def eval(self, script, *args):
            result = await redis.eval(script, *args)
            if script == FAIR_ACQUIRE_SCRIPT:
                raise RedisError("simulated lost reply after grant")
            return result

        async def aclose(self):
            pass

    monkeypatch.setattr(scheduler.Redis, "from_url", lambda *a, **kw: LostResponse())
    called = []
    with pytest.raises(RedisError):
        await scheduler.run_vision_request(
            settings, operation="beautify_planning", request=lambda: called.append(True),
        )
    assert not called
    assert await redis.zcard(keys[1]) == 0
    assert await redis.zcard(keys[4]) == 0
    assert await redis.zcard(keys[0]) == 1


async def test_real_parallel_requests_keep_payloads_and_capacity(harness):
    redis, settings, keys = harness
    settings.ai_tagging_global_concurrency = 3
    lock = threading.Lock()
    active = peak = completed = 0
    def request(payload):
        nonlocal active, peak, completed
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1
            completed += 1
        return payload
    payloads = [{"image": i, "labels": ["fixed"], "bytes": b"unchanged"} for i in range(12)]
    operations = ["routed_filter", "beautify_planning", "content_analysis_batch"]
    async def one(i, payload):
        return await scheduler.run_vision_request(
            settings, operation=operations[i % 3], request=lambda: request(payload),
        )
    results = await asyncio.wait_for(asyncio.gather(
        *(one(i, payload) for i, payload in enumerate(payloads)),
    ), timeout=10)
    assert all(actual is expected for actual, expected in zip(results, payloads, strict=True))
    assert completed == 12 and 1 < peak <= 3
    assert await redis.zcard(keys[0]) == 12
    assert await redis.zcard(keys[1]) == 0
    assert await redis.zcard(keys[4]) == 0


async def test_wait_reason_telemetry_is_measured(harness):
    redis, settings, keys = harness
    await block(harness)
    telemetry = {}
    task = asyncio.create_task(scheduler.run_vision_request(
        settings, operation="routed_filter", request=lambda: "ok", telemetry=telemetry,
    ))
    await wait_until_queued(redis, keys[4])
    await redis.zrem(keys[1], "holder")
    assert await asyncio.wait_for(task, 3) == "ok"
    assert telemetry["scheduler_wait_by_reason_ms"]["concurrency"] > 0
    assert telemetry["scheduler_wait_by_reason_ms"]["rpm"] == 0


async def test_cancelled_running_thread_keeps_lease(harness):
    redis, settings, keys = harness
    started, finish = threading.Event(), threading.Event()
    def request():
        started.set()
        finish.wait(timeout=3)
        return "done"
    task = asyncio.create_task(scheduler.run_vision_request(
        settings, operation="routed_filter", request=request,
    ))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await redis.zcard(keys[1]) == 1
        assert (await acquire(harness, "next", 1))[0] == 0
    finally:
        finish.set()
