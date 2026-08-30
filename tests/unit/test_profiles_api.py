from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.api import profiles as profiles_api
from src.core.config import Settings, get_settings
from src.main import app
from src.services.managed_profiles import (
    CompiledProfile,
    ManagedProfileError,
    ManagedProfileService,
)


def test_list_profiles_returns_only_managed_filter_profiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_list(_service, profile_type: str):
        assert profile_type == "filter"
        return [
            SimpleNamespace(
                id="managed-filter",
                name="Managed filter",
                description="Managed filter description",
                version=1,
                status="active",
            )
        ]

    monkeypatch.setattr(profiles_api.ManagedProfileService, "list", fake_list)
    app.dependency_overrides[get_settings] = lambda: Settings(
        api_key="test-api-key",
        profiles_directory="profiles",
    )
    client = TestClient(app)

    response = client.get("/api/v1/filter-profiles", headers={"X-API-Key": "test-api-key"})

    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert all(profile["id"] and profile["description"] for profile in response.json())


def test_list_similarity_profiles_only_returns_matching_profiles() -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        api_key="test-api-key",
        profiles_directory="profiles",
    )
    client = TestClient(app)

    response = client.get(
        "/api/v1/similarity-profiles",
        headers={"X-API-Key": "test-api-key"},
    )

    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert [profile["id"] for profile in response.json()] == ["library_similarity_v2"]


@pytest.mark.asyncio
async def test_successful_natural_language_preview_can_always_be_saved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_compile(*_args, **_kwargs) -> CompiledProfile:
        return CompiledProfile(
            description="只保留厨房照片",
            config={"id": "preview"},
            unsupported=["旧解析器误判的内容规则"],
        )

    monkeypatch.setattr(profiles_api.ManagedProfileService, "compile", fake_compile)

    result = await profiles_api._preview(
        "filter",
        SimpleNamespace(instruction="只保留厨房照片"),
        object(),
        Settings(),
    )

    assert result.can_save is True


@pytest.mark.asyncio
async def test_only_two_active_processing_standards_can_be_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def two_active_standards(*_args, **_kwargs):
        return [SimpleNamespace(id="one"), SimpleNamespace(id="two")]

    monkeypatch.setattr(ManagedProfileService, "list", two_active_standards)
    service = ManagedProfileService(SimpleNamespace(), Settings())

    with pytest.raises(ManagedProfileError, match="只允许启用两套"):
        await service.create(
            "standard",
            name="第三套",
            instruction="第三套启动规则",
            description="不应保存",
            config={
                "activation_rule": "第三套启动规则",
                "filter_rule": "第三套过滤规则",
            },
        )
