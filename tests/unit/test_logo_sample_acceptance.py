from scripts.verify_logo_sample_acceptance import _match_boxes


def test_sample_acceptance_matches_boxes_once_and_counts_errors() -> None:
    expected = [(10, 10, 30, 30), (50, 50, 80, 80)]
    predicted = [(9, 9, 31, 31), (0, 60, 8, 70)]

    true_positives, false_positives, false_negatives, overlaps = _match_boxes(
        expected, predicted, 0.5
    )

    assert true_positives == 1
    assert false_positives == 1
    assert false_negatives == 1
    assert overlaps[0] > 0.8
