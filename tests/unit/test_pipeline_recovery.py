from src.services.jobs import dispatch
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

    dispatch.ProvisionalEmbeddingTaskPublisher().publish("img_test")
    dispatch.EmbeddingTaskPublisher().publish("img_test")
    dispatch.LibraryAssetTaskPublisher().publish("img_test")

    assert priorities == [
        ("image.generate_embedding", 5),
        ("image.generate_embedding", 9),
        ("library.process_asset", 1),
    ]
