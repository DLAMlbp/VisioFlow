from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.models.image_item import ImageItem
from src.services.jobs import dispatch
from src.workers import celery_app as celery_app_module
from src.workers import cleanup


class _RecoveryResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self.values


class _PendingRecoverySession:
    def __init__(self, items):
        self.items = items
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return _RecoveryResult(self.items)


class _PendingRecoveryRepository:
    def __init__(self, *, match_ready: bool = False):
        self.match_ready = match_ready
        self.claimed: list[str] = []

    async def claim_match_if_ready(self, image_id: str) -> bool:
        self.claimed.append(image_id)
        return self.match_ready


@pytest.mark.asyncio
async def test_pending_recovery_republishes_lost_beautify_message(monkeypatch) -> None:
    now = datetime.now(UTC)
    item = ImageItem(
        id="img_lost_beautify",
        job_id="job_active",
        object_key="uploads/lost.jpg",
        status="filtered",
        beautify_plan_status="pending",
        analysis_status="completed",
        embedding_status="provisional",
        match_status="pending",
        updated_at=now - timedelta(seconds=61),
    )
    published: list[tuple[str, str]] = []
    released: list[tuple[str, str]] = []

    class Publisher:
        def publish(self, image_id: str) -> bool:
            published.append((type(self).__name__, image_id))
            return True

    monkeypatch.setattr(cleanup, "BeautifyPlanTaskPublisher", Publisher)
    monkeypatch.setattr(
        cleanup,
        "release_recovery_lease",
        lambda publisher, image_id: released.append((publisher, image_id)),
    )
    monkeypatch.setattr(cleanup, "emit_metric", lambda *_args, **_kwargs: None)
    session = _PendingRecoverySession([item])
    repository = _PendingRecoveryRepository(match_ready=False)

    recovered = await cleanup._recover_pending_dispatches(
        session,
        repository,
        now=now,
        settings=SimpleNamespace(pipeline_stale_seconds=60),
    )

    assert recovered == 1
    assert published == [("Publisher", item.id)]
    assert released == [("Publisher", item.id)]
    assert repository.claimed == [item.id]
    sql = str(session.statement)
    assert "image_jobs.cancel_requested_at IS NULL" in sql
    assert "image_items.updated_at <" in sql
    assert "beautify_plan_status" in sql


@pytest.mark.asyncio
async def test_pending_recovery_republishes_each_ready_parallel_stage(monkeypatch) -> None:
    now = datetime.now(UTC)
    item = ImageItem(
        id="img_parallel_pending",
        job_id="job_active",
        object_key="uploads/pending.jpg",
        status="filtered",
        beautify_plan_status="pending",
        analysis_status="pending",
        embedding_status="pending",
        match_status="pending",
        updated_at=now - timedelta(seconds=61),
    )
    published: list[tuple[str, str]] = []

    def publisher_type(name: str):
        return type(
            name,
            (),
            {
                "publish": lambda self, image_id: (
                    published.append((type(self).__name__, image_id)) or True
                )
            },
        )

    for name in (
        "BeautifyPlanTaskPublisher",
        "AnalysisTaskPublisher",
        "EmbeddingTaskPublisher",
        "MatchTaskPublisher",
    ):
        monkeypatch.setattr(cleanup, name, publisher_type(name))
    monkeypatch.setattr(cleanup, "release_recovery_lease", lambda *_args: None)
    monkeypatch.setattr(cleanup, "emit_metric", lambda *_args, **_kwargs: None)

    recovered = await cleanup._recover_pending_dispatches(
        _PendingRecoverySession([item]),
        _PendingRecoveryRepository(match_ready=True),
        now=now,
        settings=SimpleNamespace(pipeline_stale_seconds=60),
    )

    assert recovered == 4
    assert published == [
        ("BeautifyPlanTaskPublisher", item.id),
        ("AnalysisTaskPublisher", item.id),
        ("EmbeddingTaskPublisher", item.id),
        ("MatchTaskPublisher", item.id),
    ]


def test_recovery_republishes_lost_messages_for_each_pipeline_stage(monkeypatch) -> None:
    published: dict[str, list[str]] = {}

    def capture(publisher, entity_ids):
        published[type(publisher).__name__] = entity_ids

    monkeypatch.setattr(cleanup, "_publish_many", capture)

    cleanup._publish_pipeline_recovery(
        undispatched_jobs=["job_dispatch"],
        metadata=["img_metadata"],
        completion=["img_completion"],
        routed_processing=["img_filter"],
        ranking=["job_ranking", "job_ranking"],
        beautify_plan=["img_plan"],
        redaction=["img_redaction", "img_redaction"],
        inpaint=["img_inpaint", "img_inpaint"],
        enhancement=["img_enhance", "img_enhance"],
        render=["img_render", "img_render"],
        analysis=["img_legacy_analysis"],
        embedding=["img_embedding"],
        matching=["img_match", "img_match"],
    )

    assert published["RankingTaskPublisher"] == ["job_ranking"]
    assert published["RedactionDetectionTaskPublisher"] == ["img_redaction"]
    assert published["InpaintTaskPublisher"] == ["img_inpaint"]
    assert published["EnhancementTaskPublisher"] == ["img_enhance"]
    assert published["RenderTaskPublisher"] == ["img_render"]
    assert published["EmbeddingTaskPublisher"] == ["img_embedding"]
    assert published["MatchTaskPublisher"] == ["img_match"]


def test_enhancement_recovery_uses_the_matching_stage_publisher() -> None:
    assert type(cleanup._enhancement_recovery_publisher("redaction")).__name__ == (
        "RedactionDetectionTaskPublisher"
    )
    assert type(cleanup._enhancement_recovery_publisher("inpaint")).__name__ == (
        "InpaintTaskPublisher"
    )
    assert type(cleanup._enhancement_recovery_publisher("enhance")).__name__ == (
        "EnhancementTaskPublisher"
    )
    assert type(cleanup._enhancement_recovery_publisher("render")).__name__ == (
        "RenderTaskPublisher"
    )
    assert cleanup._enhancement_recovery_publisher("completed") is None


@pytest.mark.asyncio
async def test_stalled_enhancement_recovery_keeps_the_current_stage() -> None:
    class Result:
        def __init__(self, values):
            self.values = values

        def scalars(self):
            return self.values

    class Session:
        def __init__(self) -> None:
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return Result(["img_stalled"] if len(self.statements) == 1 else ["img_pending"])

    session = Session()
    recovered = await cleanup._recover_enhancement_stage(
        session,
        "inpaint",
        datetime(2026, 9, 3, tzinfo=UTC),
    )

    assert recovered == ["img_stalled", "img_pending"]
    update_sql = str(session.statements[0])
    select_sql = str(session.statements[1])
    assert "enhancement_stage_started_at <" in update_sql
    assert "enhancement_stage_started_at IS NULL" in select_sql
    assert "inpaint" in session.statements[0].compile().params.values()
    assert "inpaint" in session.statements[1].compile().params.values()


def test_pipeline_publishers_use_independent_stage_queues(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def send_task(name, *, args, queue, **_kwargs):
        assert args == ["img_test"]
        calls.append((name, queue))

    monkeypatch.setattr(dispatch.celery_app, "send_task", send_task)
    monkeypatch.setattr(
        dispatch, "acquire_recovery_lease", lambda *_args, **_kwargs: True
    )

    dispatch.CompletionTaskPublisher().publish("img_test")
    dispatch.RoutedProcessingTaskPublisher().publish("img_test")
    dispatch.RedactionDetectionTaskPublisher().publish("img_test")
    dispatch.InpaintTaskPublisher().publish("img_test")
    dispatch.EnhancementTaskPublisher().publish("img_test")
    dispatch.RenderTaskPublisher().publish("img_test")
    dispatch.AnalysisTaskPublisher().publish("img_test")
    dispatch.EmbeddingTaskPublisher().publish("img_test")
    dispatch.MatchTaskPublisher().publish("img_test")

    assert calls == [
        ("image.classify_completion", "classification"),
        ("image.apply_routed_processing", "filtering"),
        ("image.detect_redaction", "redaction"),
        ("image.inpaint_watermark", "inpaint"),
        ("image.enhance", "enhance"),
        ("image.render_image", "render"),
        ("image.analyze_content", "analysis"),
        ("image.generate_embedding", "openclip"),
        ("image.match_library", "matching"),
    ]


def test_library_work_uses_its_dedicated_queue(monkeypatch) -> None:
    calls: list[tuple[str, str, int | None]] = []

    def send_task(name, *, args, queue, priority=None, **_kwargs):
        assert args == ["img_test"]
        calls.append((name, queue, priority))

    monkeypatch.setattr(dispatch.celery_app, "send_task", send_task)
    leased: set[tuple[str, str]] = set()

    def acquire(publisher_name: str, entity_id: str) -> bool:
        key = (publisher_name, entity_id)
        if key in leased:
            return False
        leased.add(key)
        return True

    monkeypatch.setattr(dispatch, "acquire_recovery_lease", acquire)

    dispatch.ProvisionalEmbeddingTaskPublisher().publish("img_test")
    dispatch.EmbeddingTaskPublisher().publish("img_test")
    dispatch.LibraryAssetTaskPublisher().publish("img_test")

    assert calls == [
        ("image.generate_embedding", "openclip", 5),
        ("library.process_asset", "library", None),
    ]


def test_normal_publish_uses_cross_run_lease(monkeypatch) -> None:
    published: list[str] = []
    acquired: list[tuple[str, str]] = []

    def acquire(publisher_name: str, entity_id: str) -> bool:
        acquired.append((publisher_name, entity_id))
        return len(acquired) == 1

    monkeypatch.setattr(dispatch, "acquire_recovery_lease", acquire)
    monkeypatch.setattr(
        dispatch.celery_app,
        "send_task",
        lambda _name, *, args, **_kwargs: published.append(args[0]),
    )

    publisher = dispatch.CompletionTaskPublisher()
    publisher.publish("img_test")
    publisher.publish("img_test")

    assert acquired == [
        ("CompletionTaskPublisher", "img_test"),
        ("CompletionTaskPublisher", "img_test"),
    ]
    assert published == ["img_test"]


def test_library_asset_publish_uses_cross_run_lease(monkeypatch) -> None:
    published: list[str] = []
    acquired: set[tuple[str, str]] = set()

    def acquire(publisher_name: str, entity_id: str) -> bool:
        key = (publisher_name, entity_id)
        if key in acquired:
            return False
        acquired.add(key)
        return True

    monkeypatch.setattr(dispatch, "acquire_recovery_lease", acquire)
    monkeypatch.setattr(
        dispatch.celery_app,
        "send_task",
        lambda _name, *, args, **_kwargs: published.append(args[0]),
    )

    publisher = dispatch.LibraryAssetTaskPublisher()
    publisher.publish("ast_test")
    publisher.publish("ast_test")

    assert published == ["ast_test"]


def test_pipeline_publish_releases_lease_when_broker_publish_fails(monkeypatch) -> None:
    released: list[tuple[str, str]] = []

    monkeypatch.setattr(dispatch, "acquire_recovery_lease", lambda *_args: True)
    monkeypatch.setattr(
        dispatch,
        "release_recovery_lease",
        lambda publisher_name, entity_id: released.append((publisher_name, entity_id)),
    )
    monkeypatch.setattr(
        dispatch.celery_app,
        "send_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("broker unavailable")),
    )

    try:
        dispatch.CompletionTaskPublisher().publish("img_test")
    except RuntimeError as exc:
        assert str(exc) == "broker unavailable"
    else:
        raise AssertionError("publish failure was not propagated")

    assert released == [("CompletionTaskPublisher", "img_test")]


def test_retry_keeps_pipeline_recovery_lease(monkeypatch) -> None:
    released: list[tuple[str, str]] = []
    monkeypatch.setattr(
        dispatch,
        "release_recovery_lease_for_task",
        lambda task_name, entity_id: released.append((task_name, entity_id)),
    )
    task = type("Task", (), {"name": "image.classify_completion"})()

    celery_app_module.release_pipeline_recovery_lease(
        task=task, args=["img_test"], state="RETRY"
    )
    assert released == []

    celery_app_module.release_pipeline_recovery_lease(
        task=task, args=["img_test"], state="SUCCESS"
    )
    assert released == [("image.classify_completion", "img_test")]


def test_callback_publisher_uses_dedicated_queue(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    leases: list[tuple[str, str, int | None]] = []
    monkeypatch.setattr(
        dispatch,
        "acquire_recovery_lease",
        lambda publisher_name, entity_id, *, lease_seconds=None: (
            leases.append((publisher_name, entity_id, lease_seconds)) or True
        ),
    )
    monkeypatch.setattr(
        dispatch,
        "get_settings",
        lambda: type("Settings", (), {"pipeline_stale_seconds": 900})(),
    )
    monkeypatch.setattr(
        dispatch.celery_app,
        "send_task",
        lambda name, *, args, queue, **_kwargs: calls.append((name, queue)),
    )

    dispatch.CallbackTaskPublisher().publish("job_test")

    assert calls == [("image.deliver_callback", "callback")]
    assert leases == [("CallbackTaskPublisher", "job_test", 900)]


def test_duplicate_callback_publish_is_suppressed(monkeypatch) -> None:
    published: list[str] = []
    acquired = False

    def acquire(_publisher_name, _entity_id, *, lease_seconds=None):
        nonlocal acquired
        assert lease_seconds == 900
        if acquired:
            return False
        acquired = True
        return True

    monkeypatch.setattr(dispatch, "acquire_recovery_lease", acquire)
    monkeypatch.setattr(
        dispatch,
        "get_settings",
        lambda: type("Settings", (), {"pipeline_stale_seconds": 900})(),
    )
    monkeypatch.setattr(
        dispatch.celery_app,
        "send_task",
        lambda _name, *, args, **_kwargs: published.append(args[0]),
    )

    publisher = dispatch.CallbackTaskPublisher()
    publisher.publish("job_test")
    publisher.publish("job_test")

    assert published == ["job_test"]


def test_callback_completion_releases_publish_lease(monkeypatch) -> None:
    released: list[tuple[str, str]] = []
    monkeypatch.setattr(
        dispatch,
        "release_recovery_lease",
        lambda publisher_name, entity_id: released.append((publisher_name, entity_id)),
    )

    dispatch.release_recovery_lease_for_task("image.deliver_callback", "job_test")

    assert released == [("CallbackTaskPublisher", "job_test")]


def test_library_completion_releases_publish_leases(monkeypatch) -> None:
    released: list[tuple[str, str]] = []
    monkeypatch.setattr(
        dispatch,
        "release_recovery_lease",
        lambda publisher_name, entity_id: released.append((publisher_name, entity_id)),
    )

    dispatch.release_recovery_lease_for_task("library.process_asset", "ast_test")
    dispatch.release_recovery_lease_for_task(
        "library.rebuild_group_prototypes", "grp_test"
    )

    assert released == [
        ("LibraryAssetTaskPublisher", "ast_test"),
        ("LibraryGroupPrototypeTaskPublisher", "grp_test"),
    ]
