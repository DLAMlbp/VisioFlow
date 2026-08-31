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


def test_calibration_recommends_thresholds_only_when_precision_target_is_met() -> None:
    report = calibrate(
        summarize_queries(_rows(38, 2)),
        target_precision=0.97,
        min_auto_matches=30,
    )

    assert report["status"] == "recommended"
    assert report["recommendation"]["precision"] == 1.0
    assert report["recommendation"]["auto_matches"] == 38


def test_calibration_reports_insufficient_evidence_without_fabricating_accuracy() -> None:
    report = calibrate(
        summarize_queries(_rows(20, 20)),
        target_precision=0.97,
        min_auto_matches=30,
    )

    assert report["status"] == "insufficient_evidence"
    assert report["recommendation"] is None
