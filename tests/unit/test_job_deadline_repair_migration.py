import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    ROOT / "alembic" / "versions" / "20260904_0043_repair_job_deadline_columns.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("job_deadline_repair", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_adds_missing_job_deadline_columns(monkeypatch) -> None:
    migration = _load_migration()
    added: list[str] = []
    indexes: list[tuple[str, str, list[str]]] = []
    executed: list[str] = []

    class Inspector:
        def get_columns(self, _table: str):
            return [{"name": "id"}, {"name": "status"}, {"name": "created_at"}]

    monkeypatch.setattr(migration.op, "get_bind", lambda: object())
    monkeypatch.setattr(migration.sa, "inspect", lambda _connection: Inspector())
    monkeypatch.setattr(
        migration.op,
        "add_column",
        lambda table, column: added.append(f"{table}.{column.name}"),
    )
    monkeypatch.setattr(
        migration.op,
        "create_index",
        lambda name, table, columns: indexes.append((name, table, columns)),
    )
    monkeypatch.setattr(migration.op, "execute", lambda sql: executed.append(sql))

    migration.upgrade()

    assert added == [
        "image_jobs.deadline_at",
        "image_jobs.failed_node",
        "image_jobs.failure_code",
        "image_jobs.failure_message",
        "image_jobs.failed_image_id",
        "image_jobs.failure_duration_ms",
        "image_jobs.upstream_status_code",
        "image_jobs.failed_at",
    ]
    assert indexes == [("ix_image_jobs_deadline_at", "image_jobs", ["deadline_at"])]
    assert executed


def test_upgrade_is_safe_when_job_deadline_columns_exist(monkeypatch) -> None:
    migration = _load_migration()
    complete = [{"name": name} for name in migration.REQUIRED_COLUMNS]

    class Inspector:
        def get_columns(self, _table: str):
            return complete

    monkeypatch.setattr(migration.op, "get_bind", lambda: object())
    monkeypatch.setattr(migration.sa, "inspect", lambda _connection: Inspector())
    monkeypatch.setattr(
        migration.op,
        "add_column",
        lambda *_args: (_ for _ in ()).throw(AssertionError("unexpected column")),
    )
    monkeypatch.setattr(
        migration.op,
        "create_index",
        lambda *_args: (_ for _ in ()).throw(AssertionError("unexpected index")),
    )
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda *_args: (_ for _ in ()).throw(AssertionError("unexpected sql")),
    )

    migration.upgrade()
