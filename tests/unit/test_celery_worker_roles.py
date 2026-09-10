from src.workers.celery_app import _ALL_TASK_IMPORTS, _task_imports, celery_app


def test_control_worker_only_imports_control_tasks() -> None:
    assert _task_imports("control") == (
        "src.workers.control",
        "src.workers.callbacks",
    )


def test_beat_does_not_import_worker_implementations() -> None:
    assert _task_imports("beat") == ()


def test_processing_workers_only_import_their_task_modules() -> None:
    assert _task_imports("preprocess") == ("src.workers.preprocess",)
    assert _task_imports("classification") == (
        "src.workers.completion",
        "src.workers.processing",
    )
    assert _task_imports("filtering") == ("src.workers.processing",)
    assert _task_imports("beautify_plan") == ("src.workers.beautify_plan",)
    assert _task_imports("redaction") == ("src.workers.redaction",)
    assert _task_imports("inpaint") == ("src.workers.inpaint",)
    assert _task_imports("enhance") == ("src.workers.enhance",)
    assert _task_imports("render") == ("src.workers.render",)
    assert _task_imports("analysis") == ("src.workers.analysis",)
    assert _task_imports("matching") == ("src.workers.matching",)
    assert _task_imports("cleanup") == ("src.workers.cleanup",)


def test_openclip_and_library_workers_have_isolated_task_registries() -> None:
    assert _task_imports("openclip") == ("src.workers.matching",)
    assert _task_imports("library") == ("src.workers.library",)


def test_library_maintenance_is_scheduled_on_the_dedicated_queue() -> None:
    schedule = celery_app.conf.beat_schedule

    assert schedule["backfill-library-content-features"]["options"]["queue"] == "library"
    assert schedule["backfill-library-group-prototypes"]["options"]["queue"] == "library"


def test_unknown_worker_role_keeps_complete_registry_for_compatibility() -> None:
    assert _task_imports("") == _ALL_TASK_IMPORTS
