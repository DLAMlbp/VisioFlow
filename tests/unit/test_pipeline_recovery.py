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
