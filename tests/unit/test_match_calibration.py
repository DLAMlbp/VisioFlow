from scripts.calibrate_library_matching import calibrate, summarize_queries


def _rows(correct_count: int, incorrect_count: int):
    rows = []
    for index in range(correct_count):
        query_id = f"correct_{index}"
        rows.extend(
            [
                {
                    "query_id": query_id,
                    "expected_asset_id": "expected",
                    "candidate_asset_id": "expected",
                    "similarity_score": 0.9,
                },
                {
                    "query_id": query_id,
                    "expected_asset_id": "expected",
                    "candidate_asset_id": "other",
                    "similarity_score": 0.7,
                },
            ]
        )
    for index in range(incorrect_count):
        query_id = f"incorrect_{index}"
        rows.extend(
            [
                {
                    "query_id": query_id,
                    "expected_asset_id": "expected",
                    "candidate_asset_id": "other",
                    "similarity_score": 0.91,
                },
                {
                    "query_id": query_id,
                    "expected_asset_id": "expected",
                    "candidate_asset_id": "expected",
                    "similarity_score": 0.90,
                },
            ]
        )
    return rows


def _passing_rows(correct_count: int, rejected_incorrect_count: int):
    rows = _rows(correct_count, 0)
    for index in range(rejected_incorrect_count):
        query_id = f"rejected_incorrect_{index}"
        rows.extend(
            [
                {
                    "query_id": query_id,
                    "expected_asset_id": "expected",
                    "candidate_asset_id": "other",
                    "similarity_score": 0.59,
                },
                {
                    "query_id": query_id,
                    "expected_asset_id": "expected",
                    "candidate_asset_id": "expected",
                    "similarity_score": 0.58,
                },
            ]
        )
    return rows


def test_calibration_evaluates_fixed_threshold_only_when_precision_target_is_met() -> None:
    report = calibrate(
        summarize_queries(_passing_rows(38, 2)),
        target_precision=0.97,
        min_auto_matches=30,
    )

    assert report["status"] == "recommended"
    assert report["recommendation"]["precision"] == 1.0
    assert report["recommendation"]["auto_matches"] == 38
    assert report["recommendation"]["auto_threshold"] == 0.70
    assert report["recommendation"]["review_threshold"] == 0.70
    assert report["recommendation"]["minimum_group_margin"] == 0.03
    assert report["recommendation"]["decision_rule"] == (
        "runtime-equivalent semantic priority, score, group margin, core evidence, "
        "and fallback support gates"
    )


def test_calibration_excludes_ambiguous_group_matches_with_margin_gate() -> None:
    report = calibrate(
        summarize_queries(_rows(38, 2)),
        target_precision=0.97,
        min_auto_matches=30,
    )

    assert report["status"] == "recommended"
    assert report["evaluation"]["auto_matches"] == 38
    assert report["evaluation"]["precision"] == 1.0


def test_calibration_reports_insufficient_evidence_without_fabricating_accuracy() -> None:
    report = calibrate(
        summarize_queries(_rows(20, 20)),
        target_precision=0.97,
        min_auto_matches=30,
    )

    assert report["status"] == "insufficient_evidence"
    assert report["recommendation"] is None


def test_calibration_requires_group_margin_and_core_evidence() -> None:
    rows = [
        {
            "query_id": "low_visual_small_margin",
            "expected_asset_id": "expected",
            "candidate_asset_id": "expected",
            "similarity_score": 0.40,
            "final_score": 0.60,
        },
        {
            "query_id": "low_visual_small_margin",
            "expected_asset_id": "expected",
            "candidate_asset_id": "other",
            "similarity_score": 0.95,
            "final_score": 0.599999,
        },
    ]

    report = calibrate(
        summarize_queries(rows),
        min_auto_matches=1,
        target_review_recall=1.0,
    )

    assert report["status"] == "insufficient_evidence"
    assert report["evaluation"]["auto_matches"] == 0


def test_calibration_accepts_group_id_columns_and_rejects_failed_core_gate() -> None:
    rows = [
        {
            "query_id": "query_1",
            "expected_group_id": "expected_group",
            "candidate_group_id": "expected_group",
            "similarity_score": 0.90,
            "core_requirements_passed": False,
        },
        {
            "query_id": "query_1",
            "expected_group_id": "expected_group",
            "candidate_group_id": "other_group",
            "similarity_score": 0.80,
            "core_requirements_passed": True,
        },
    ]

    report = calibrate(
        summarize_queries(rows),
        min_auto_matches=1,
        target_review_recall=1.0,
    )

    assert report["status"] == "insufficient_evidence"
    assert report["evaluation"]["auto_matches"] == 1
    assert report["evaluation"]["precision"] == 0.0


def test_calibration_recomputes_v4_score_from_reliability_and_coverage() -> None:
    rows = [
        {
            "query_id": "query_1",
            "expected_asset_id": "expected",
            "candidate_asset_id": "expected",
            "similarity_score": 0.6632,
            "feature_score": 0.4379,
            "feature_reliability": 0.15,
            "feature_coverage": 0.15,
        }
    ]

    result = summarize_queries(rows)[0]

    assert result.top1_score > 0.60
    assert result.top1_feature_reliability == 0.15
    assert result.top1_feature_coverage == 0.15
