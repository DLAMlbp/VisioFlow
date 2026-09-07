from scripts.benchmark_group_matching import benchmark


def test_group_matching_decision_stays_within_cpu_latency_budget() -> None:
    result = benchmark(
        iterations=20,
        groups=167,
        prototypes_per_group=6,
        allowed_delta_ms=10.0,
    )

    assert result.status == "passed"
    assert result.index_p95_ms < 2.0
    assert result.group_p95_ms < 10.0
