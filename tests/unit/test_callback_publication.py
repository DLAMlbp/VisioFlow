from types import SimpleNamespace

import pytest

from src.repositories import jobs as jobs_module
from src.repositories.jobs import ImageJobRepository


class _Result:
    def __init__(self, *, row=None, rowcount=0):
        self._row = row
        self.rowcount = rowcount

    def one_or_none(self):
        return self._row


class _Session:
    def __init__(self, *, callback_rowcount: int = 1):
        self.callback_rowcount = callback_rowcount
        self.execute_count = 0
        self.committed = False

    async def execute(self, _statement):
        self.execute_count += 1
        if self.execute_count == 1:
            return _Result(
                row=SimpleNamespace(processed_count=1, total_count=1, status="processing")
            )
        if self.execute_count == 2:
            return _Result(rowcount=1)
        return _Result(rowcount=self.callback_rowcount)

    async def scalar(self, _statement):
        return 0

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_terminal_job_publishes_callback_after_commit(monkeypatch) -> None:
    session = _Session()
    published: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        jobs_module,
        "_publish_callback_task",
        lambda job_id: published.append((job_id, session.committed)),
    )

    completed = await ImageJobRepository(session).complete_job_if_finished("job_test")

    assert completed is True
    assert published == [("job_test", True)]


@pytest.mark.asyncio
async def test_terminal_job_without_callback_does_not_publish(monkeypatch) -> None:
    session = _Session(callback_rowcount=0)
    published: list[str] = []
    monkeypatch.setattr(jobs_module, "_publish_callback_task", published.append)

    completed = await ImageJobRepository(session).complete_job_if_finished("job_test")

    assert completed is True
    assert published == []


@pytest.mark.asyncio
async def test_callback_publish_failure_keeps_terminal_commit(monkeypatch, caplog) -> None:
    session = _Session()
    monkeypatch.setattr(
        jobs_module,
        "_publish_callback_task",
        lambda _job_id: (_ for _ in ()).throw(RuntimeError("broker unavailable")),
    )

    completed = await ImageJobRepository(session).complete_job_if_finished("job_test")

    assert completed is True
    assert session.committed is True
    assert "recovery will retry" in caplog.text
