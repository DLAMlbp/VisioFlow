from src.services.jobs import dispatch
from src.workers import celery_app as celery_app_module
from src.workers import cleanup


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
        enhancement=["img_enhance", "img_enhance"],
        analysis=["img_legacy_analysis"],
        embedding=["img_embedding"],
        matching=["img_match", "img_match"],
    )

    assert published["RankingTaskPublisher"] == ["job_ranking"]
    assert published["EnhancementTaskPublisher"] == ["img_enhance"]
    assert published["EmbeddingTaskPublisher"] == ["img_embedding"]
    assert published["MatchTaskPublisher"] == ["img_match"]


def test_pipeline_publishers_use_independent_stage_queues(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def send_task(name, *, args, queue, **_kwargs):
        assert args == ["img_test"]
        calls.append((name, queue))

    monkeypatch.setattr(dispatch.celery_app, "send_task", send_task)
    monkeypatch.setattr(dispatch, "acquire_recovery_lease", lambda *_args: True)

    dispatch.CompletionTaskPublisher().publish("img_test")
    dispatch.RoutedProcessingTaskPublisher().publish("img_test")
    dispatch.AnalysisTaskPublisher().publish("img_test")
    dispatch.EmbeddingTaskPublisher().publish("img_test")
    dispatch.MatchTaskPublisher().publish("img_test")

    assert calls == [
        ("image.classify_completion", "classification"),
        ("image.apply_routed_processing", "filtering"),
        ("image.analyze_content", "analysis"),
        ("image.generate_embedding", "openclip"),
        ("image.match_library", "matching"),
    ]


def test_final_embedding_overtakes_provisional_and_library_work(monkeypatch) -> None:
    priorities: list[tuple[str, int]] = []

    def send_task(name, *, args, queue, priority, **_kwargs):
        assert args == ["img_test"]
        assert queue == "openclip"
        priorities.append((name, priority))

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

    assert priorities == [("image.generate_embedding", 5), ("library.process_asset", 1)]


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
    monkeypatch.setattr(
        dispatch.celery_app,
        "send_task",
        lambda name, *, args, queue: calls.append((name, queue)),
    )

    dispatch.CallbackTaskPublisher().publish("job_test")

    assert calls == [("image.deliver_callback", "callback")]
