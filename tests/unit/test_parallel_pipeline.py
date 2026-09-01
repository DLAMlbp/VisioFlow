from types import SimpleNamespace

import pytest

from src.repositories.jobs import ImageJobRepository


class _Result:
    def __init__(self, *, rowcount: int = 0, scalar=None) -> None:
        self.rowcount = rowcount
        self._scalar = scalar

    def scalar_one_or_none(self):
        return self._scalar


class _FinalizeRepository(ImageJobRepository):
    def __init__(self, session) -> None:
        super().__init__(session)
        self.completed_jobs: list[str] = []

    async def complete_job_if_finished(self, job_id: str) -> bool:
        self.completed_jobs.append(job_id)
        return True


@pytest.mark.asyncio
async def test_post_filter_branches_are_initialized_by_one_conditional_update() -> None:
    class Session:
        def __init__(self) -> None:
            self.statement = None
            self.commits = 0

        async def execute(self, statement):
            self.statement = statement
            return _Result(rowcount=1)

        async def commit(self) -> None:
            self.commits += 1

    session = Session()
    repository = ImageJobRepository(session)  # type: ignore[arg-type]

    queued = await repository.queue_post_filter_branches(
        "img_test", similarity_enabled=True
    )

    assert queued is True
    assert session.commits == 1
    params = session.statement.compile().params
    assert "pending" in params.values()
    sql = str(session.statement)
    assert "beautify_plan_status" in sql
    assert "analysis_status" in sql
    assert "embedding_status" in sql
    assert "match_status" in sql


@pytest.mark.asyncio
async def test_pre_enhancement_embedding_is_persisted_as_provisional() -> None:
    class Session:
        def __init__(self) -> None:
            self.statement = None

        async def execute(self, statement):
            self.statement = statement
            return _Result(rowcount=1)

        async def commit(self) -> None:
            pass

    session = Session()
    repository = ImageJobRepository(session)  # type: ignore[arg-type]

    await repository.complete_embedding_stage(
        "img_test",
        embedding=[0.0] * 512,
        embedding_version="openclip-test",
        provisional=True,
    )

    assert "provisional" in session.statement.compile().params.values()


@pytest.mark.asyncio
async def test_delivery_embedding_replaces_only_a_provisional_vector() -> None:
    class Session:
        def __init__(self) -> None:
            self.statement = None
            self.commits = 0

        async def execute(self, statement):
            self.statement = statement
            return _Result(rowcount=1)

        async def commit(self) -> None:
            self.commits += 1

    session = Session()
    repository = ImageJobRepository(session)  # type: ignore[arg-type]

    queued = await repository.queue_final_embedding("img_test")

    assert queued is True
    assert session.commits == 1
    sql = str(session.statement)
    assert "image_items.status" in sql
    assert "embedding_status" in sql
    assert "match_status" in sql
    params = list(session.statement.compile().params.values())
    assert "enhanced" in params
    assert ["provisional", "provisional_failed"] in params
    assert "pending" in params


@pytest.mark.asyncio
async def test_finalizer_requires_enhancement_and_both_semantic_branches() -> None:
    stored_result = SimpleNamespace(reasons_json=["增强完成"])

    class Session:
        def __init__(self) -> None:
            self.calls = 0
            self.statements = []
            self.commits = 0
            self.rollbacks = 0

        async def execute(self, statement):
            self.calls += 1
            self.statements.append(statement)
            if self.calls == 1:
                return _Result(rowcount=1)
            if self.calls in {2, 3}:
                return _Result(scalar=stored_result)
            return _Result(rowcount=1)

        async def commit(self) -> None:
            self.commits += 1

        async def rollback(self) -> None:
            self.rollbacks += 1

    session = Session()
    repository = _FinalizeRepository(session)  # type: ignore[arg-type]
    item = SimpleNamespace(id="img_test", job_id="job_test", status="enhanced")

    finalized = await repository.finalize_selected_if_ready(
        item, reason="素材库匹配成功"
    )

    assert finalized is True
    assert item.status == "selected"
    assert stored_result.decision == "selected"
    assert stored_result.reasons_json == ["增强完成", "素材库匹配成功"]
    assert repository.completed_jobs == ["job_test"]
    barrier_sql = str(session.statements[0])
    assert "enhance_completed_at IS NOT NULL" in barrier_sql
    assert "analysis_status" in barrier_sql
    assert "embedding_status" in barrier_sql
    assert "match_status" in barrier_sql


@pytest.mark.asyncio
async def test_finalizer_does_not_increment_counters_when_barrier_is_not_ready() -> None:
    class Session:
        def __init__(self) -> None:
            self.calls = 0
            self.rollbacks = 0

        async def execute(self, _statement):
            self.calls += 1
            return _Result(rowcount=0)

        async def commit(self) -> None:
            raise AssertionError("not-ready finalization must not commit")

        async def rollback(self) -> None:
            self.rollbacks += 1

    session = Session()
    repository = _FinalizeRepository(session)  # type: ignore[arg-type]

    finalized = await repository.finalize_selected_if_ready(
        SimpleNamespace(id="img_test", job_id="job_test"),
        reason="too early",
    )

    assert finalized is False
    assert session.calls == 1
    assert session.rollbacks == 1
    assert repository.completed_jobs == []
