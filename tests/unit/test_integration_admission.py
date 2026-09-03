from __future__ import annotations

import pytest

from src.core.config import Settings
from src.services.integration_admission import (
    IntegrationAdmissionRejected,
    enforce_integration_admission,
)


class FakePipeline:
    def __init__(self, queue_lengths: dict[str, int]) -> None:
        self.queue_lengths = queue_lengths
        self.keys: list[str] = []

    def llen(self, key: str):
        self.keys.append(key)
        return self

    def execute(self) -> list[int]:
        return [self.queue_lengths.get(key, 0) for key in self.keys]


class FakeRedis:
    def __init__(self, *, queue_lengths=None, token_result=(1, 0, 40)) -> None:
        self.queue_lengths = queue_lengths or {}
        self.token_result = token_result

    def pipeline(self, *, transaction: bool):
        assert transaction is False
        return FakePipeline(self.queue_lengths)

    def eval(self, *_args):
        return self.token_result


def settings(**updates) -> Settings:
    values = {
        "integration_admission_enabled": True,
        "integration_rate_limit_images_per_minute": 3,
        "integration_rate_limit_burst_images": 50,
        "integration_max_pipeline_queue_depth": 100,
    }
    values.update(updates)
    return Settings(**values, _env_file=None)


def test_admission_accepts_burst_below_queue_limit() -> None:
    snapshot = enforce_integration_admission(settings(), 10, client=FakeRedis())

    assert snapshot.queue_depth == 0
    assert snapshot.remaining_burst_images == 40


def test_admission_rejects_when_pipeline_would_exceed_limit() -> None:
    client = FakeRedis(
        queue_lengths={
            "classification\x06\x169": 60,
            "classification:4": 35,
        }
    )

    with pytest.raises(IntegrationAdmissionRejected) as raised:
        enforce_integration_admission(settings(), 10, client=client)

    assert raised.value.retry_after_seconds == 300
    assert raised.value.queue_depth == 95


def test_admission_counts_kombu_and_legacy_priority_lists() -> None:
    client = FakeRedis(
        queue_lengths={
            "preprocess": 1,
            "preprocess\x06\x163": 2,
            "preprocess:6": 4,
        }
    )

    snapshot = enforce_integration_admission(settings(), 1, client=client)

    assert snapshot.queue_depth == 7


def test_admission_returns_token_bucket_retry_delay() -> None:
    client = FakeRedis(token_result=(0, 125000, 3))

    with pytest.raises(IntegrationAdmissionRejected) as raised:
        enforce_integration_admission(settings(), 10, client=client)

    assert raised.value.retry_after_seconds == 125


def test_admission_can_be_disabled() -> None:
    snapshot = enforce_integration_admission(
        settings(integration_admission_enabled=False), 50, client=FakeRedis()
    )

    assert snapshot.queue_depth == 0
