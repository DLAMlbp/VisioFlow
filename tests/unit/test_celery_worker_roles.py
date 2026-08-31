from src.workers.celery_app import _ALL_TASK_IMPORTS, _task_imports


def test_control_worker_only_imports_control_tasks() -> None:
    assert _task_imports("control") == (
        "src.workers.control",
        "src.workers.callbacks",
    )


def test_beat_does_not_import_worker_implementations() -> None:
    assert _task_imports("beat") == ()


def test_processing_workers_keep_the_complete_task_registry() -> None:
    assert _task_imports("embedding") == _ALL_TASK_IMPORTS
    assert _task_imports("") == _ALL_TASK_IMPORTS
