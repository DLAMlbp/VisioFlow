import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    ROOT / "alembic" / "versions" / "20260903_0035_repair_redaction_columns.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("redaction_schema_repair", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_adds_only_missing_redaction_columns(monkeypatch) -> None:
    migration = _load_migration()
    existing = {
        "image_jobs": [{"name": "id"}],
        "upload_batches": [
            {"name": "id"},
            {"name": "redaction_profile_id"},
        ],
    }
    added: list[tuple[str, str]] = []

    class Inspector:
        def get_columns(self, table: str):
            return existing[table]

    monkeypatch.setattr(migration.op, "get_bind", lambda: object())
    monkeypatch.setattr(migration.sa, "inspect", lambda _connection: Inspector())
    monkeypatch.setattr(
        migration.op,
        "add_column",
        lambda table, column: added.append((table, column.name)),
    )

    migration.upgrade()

    assert added == [
        ("image_jobs", "redaction_profile_id"),
        ("image_jobs", "redaction_profile_snapshot"),
        ("upload_batches", "redaction_profile_snapshot"),
    ]


def test_upgrade_is_safe_when_columns_already_exist(monkeypatch) -> None:
    migration = _load_migration()
    complete = [
        {"name": "redaction_profile_id"},
        {"name": "redaction_profile_snapshot"},
    ]

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

    migration.upgrade()
