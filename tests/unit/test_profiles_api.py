from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.api import profiles as profiles_api
from src.core.config import Settings, get_settings
from src.main import app
from src.services.managed_profiles import (
    CompiledProfile,
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
async def test_multiple_reusable_branch_standards_can_be_created() -> None:
    class FakeSession:
        def add(self, row):
            self.row = row

        async def commit(self):
            return None

        async def refresh(self, row):
            return None

    session = FakeSession()
    service = ManagedProfileService(session, Settings())

    created = await service.create(
        "standard",
        name="第三套",
        instruction="由后端路由后启用",
        description="可被任务选择的分支标准",
        config={
            "activation_rule": "由后端路由后始终启用",
            "filter_rule": "过滤不符合本分支要求的图片",
        },
    )

    assert created.profile_type == "standard"
