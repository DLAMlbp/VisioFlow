"""Exercise real PostgreSQL transactions without touching application databases."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.db.base import Base
from src.models import ImageItem, ImageJob
from src.repositories.jobs import ImageJobRepository


@pytest_asyncio.fixture
async def database():
    value = os.getenv("PIPELINE_TEST_POSTGRES_URL")
    if not value:
        pytest.skip("Set PIPELINE_TEST_POSTGRES_URL to an isolated loopback throughput_test database")
    url = make_url(value)
    if url.host not in {"127.0.0.1", "localhost", "::1"} or url.database != "throughput_test":
        pytest.fail("Timestamp tests require a loopback database named throughput_test")
    schema = "throughput_test_" + uuid.uuid4().hex[:16]
    admin = create_async_engine(url, poolclass=NullPool)
    async with admin.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        await connection.run_sync(Base.metadata.create_all)
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": f"{schema},public"}},
    )
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            job = ImageJob(
                id="job_" + uuid.uuid4().hex,
                status="processing",
                filter_profile_id="test",
                beautify_profile_id="test",
                max_selected=1,
                total_count=1,
            )
            item = ImageItem(
                id="img_" + uuid.uuid4().hex,
                job_id=job.id,
                object_key="test/image.jpg",
                status="analyzing",
                preprocess_completed_at=datetime(2026, 9, 1, tzinfo=UTC),
            )
            session.add_all([job, item])
            await session.commit()
            yield session, ImageJobRepository(session), job, item
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            # Only the random schema created by this fixture, never public or user data.
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


@pytest.mark.parametrize("stage", ["classification", "beautify", "analysis", "embedding", "matching"])
async def test_stage_completion_uses_wall_clock_after_slow_external_work(database, stage):
    session, repository, _, item = database
    prefix = {"classification": "completion", "beautify": "beautify_plan", "matching": "match"}.get(
        stage, stage
    )
    started = await session.scalar(select(func.clock_timestamp()))
    setattr(item, f"{prefix}_status", "processing")
    setattr(item, f"{prefix}_started_at", started)
    if stage == "beautify":
        item.status = "beautify_planning"
    await session.commit()
    metadata_completed_at = item.preprocess_completed_at

    # A repository read starts the transaction before the slow provider call.
    assert await repository.get_config(item.job_id) is not None
    await asyncio.sleep(0.12)
    earliest_completion = await session.scalar(select(func.clock_timestamp()))
    if stage == "classification":
        assert await repository.complete_combined_classification_filter(
            item,
            model_name="test",
            completion_prompt_version="test",
            processing_prompt_version="test",
            duration_ms=120,
            completion_payload={},
            processing_payload={},
            diagnostic_json=None,
            routed_filter_profile_id="test",
            routed_filter_profile_version=1,
            completion_label="test",
            confidence=1.0,
            review_required=False,
            passed=True,
            final_score=1.0,
            reasons=[],
        )
    elif stage == "beautify":
        assert await repository.save_beautify_plan(
            item.id,
            status="completed",
            model_name="test",
            prompt_version="test",
            duration_ms=120,
            payload={},
            error_message=None,
        )
    elif stage == "analysis":
        await repository.complete_analysis_stage(item.id, succeeded=True)
    elif stage == "embedding":
        await repository.complete_embedding_stage(
            item.id, embedding=[0.0] * 512, embedding_version="test", provisional=False
        )
    else:
        await repository.complete_match_stage(item.id)

    await session.refresh(item)
    completed = getattr(item, f"{prefix}_completed_at")
    assert completed >= earliest_completion
    assert (completed - started).total_seconds() >= 0.1
    assert item.updated_at >= earliest_completion
    assert item.preprocess_completed_at == metadata_completed_at


async def test_failure_keeps_metadata_completion_and_records_actual_failure_time(database):
    session, repository, _, item = database
    item.completion_status = "processing"
    item.completion_started_at = await session.scalar(select(func.clock_timestamp()))
    await session.commit()
    metadata_completed_at = item.preprocess_completed_at
    assert await repository.get_config(item.job_id) is not None
    await asyncio.sleep(0.12)
    earliest_failure = await session.scalar(select(func.clock_timestamp()))
    assert await repository.fail_item(item, "test timeout", node="classification", code="NODE_TIMEOUT")
    await session.refresh(item)
    assert item.completion_completed_at >= earliest_failure
    assert item.preprocess_completed_at == metadata_completed_at


async def test_batch_terminal_timestamp_follows_slow_transaction(database):
    session, repository, job, item = database
    job.processed_count = 1
    item.status = "selected"
    await session.commit()
    assert await repository.get_config(job.id) is not None
    await asyncio.sleep(0.12)
    earliest_completion = await session.scalar(select(func.clock_timestamp()))
    assert await repository.complete_job_if_finished(job.id)
    await session.refresh(job)
    assert job.completed_at >= earliest_completion
    assert job.updated_at >= earliest_completion
