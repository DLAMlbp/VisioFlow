from fastapi.testclient import TestClient

from src.core.config import Settings, get_settings
from src.main import app
from src.services.library import get_library_service


class FakeLibraryService:
    deleted_group_id: str | None = None
    deleted_asset_id: str | None = None
    bulk_delete_payload = None
    reindexed_group_id: str | None = None

    async def delete_asset(self, asset_id: str) -> None:
        self.deleted_asset_id = asset_id

    async def delete_group(self, group_id: str) -> None:
        self.deleted_group_id = group_id

    async def bulk_delete_assets(self, payload):
        self.bulk_delete_payload = payload
        return {"deleted_count": 2, "failed_count": 0, "failed_asset_ids": []}

    async def reindex_failed_assets(self, *, group_id: str | None):
        self.reindexed_group_id = group_id
        return {"queued_count": 3}


def test_delete_library_asset_returns_no_content() -> None:
    service = FakeLibraryService()
    app.dependency_overrides[get_library_service] = lambda: service
    app.dependency_overrides[get_settings] = lambda: Settings(api_key="test-api-key")
    client = TestClient(app)

    response = client.delete(
        "/api/v1/library/assets/ast_test",
        headers={"X-API-Key": "test-api-key"},
    )

    app.dependency_overrides.clear()
    assert response.status_code == 204
    assert service.deleted_asset_id == "ast_test"


def test_delete_library_group_returns_no_content() -> None:
    service = FakeLibraryService()
    app.dependency_overrides[get_library_service] = lambda: service
    app.dependency_overrides[get_settings] = lambda: Settings(api_key="test-api-key")
    client = TestClient(app)

    response = client.delete(
        "/api/v1/library/groups/grp_test",
        headers={"X-API-Key": "test-api-key"},
    )

    app.dependency_overrides.clear()
    assert response.status_code == 204
    assert service.deleted_group_id == "grp_test"


def test_reindex_failed_library_assets_returns_queued_count() -> None:
    service = FakeLibraryService()
    app.dependency_overrides[get_library_service] = lambda: service
    app.dependency_overrides[get_settings] = lambda: Settings(api_key="test-api-key")
    client = TestClient(app)

    response = client.post(
        "/api/v1/library/assets/reindex-failed?group_id=grp_test",
        headers={"X-API-Key": "test-api-key"},
    )

    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"queued_count": 3}
    assert service.reindexed_group_id == "grp_test"


def test_bulk_delete_library_assets_accepts_selected_ids() -> None:
    service = FakeLibraryService()
    app.dependency_overrides[get_library_service] = lambda: service
    app.dependency_overrides[get_settings] = lambda: Settings(api_key="test-api-key")
    client = TestClient(app)

    response = client.post(
        "/api/v1/library/assets/bulk-delete",
        json={"asset_ids": ["ast_first", "ast_second"]},
        headers={"X-API-Key": "test-api-key"},
    )

    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"deleted_count": 2, "failed_count": 0, "failed_asset_ids": []}
    assert service.bulk_delete_payload.asset_ids == ["ast_first", "ast_second"]


def test_bulk_delete_library_assets_rejects_an_empty_scope() -> None:
    service = FakeLibraryService()
    app.dependency_overrides[get_library_service] = lambda: service
    app.dependency_overrides[get_settings] = lambda: Settings(api_key="test-api-key")
    client = TestClient(app)

    response = client.post(
        "/api/v1/library/assets/bulk-delete",
        json={},
        headers={"X-API-Key": "test-api-key"},
    )

    app.dependency_overrides.clear()
    assert response.status_code == 422


def test_bulk_delete_library_assets_rejects_mixed_delete_modes() -> None:
    service = FakeLibraryService()
    app.dependency_overrides[get_library_service] = lambda: service
    app.dependency_overrides[get_settings] = lambda: Settings(api_key="test-api-key")
    client = TestClient(app)

    response = client.post(
        "/api/v1/library/assets/bulk-delete",
        json={"asset_ids": ["ast_first"], "delete_all": True},
        headers={"X-API-Key": "test-api-key"},
    )

    app.dependency_overrides.clear()
    assert response.status_code == 422
    assert service.bulk_delete_payload is None


def test_manual_tag_review_routes_are_not_exposed() -> None:
    paths = app.openapi()["paths"]

    assert "/api/v1/tag-reviews" not in paths
    assert "/api/v1/tag-reviews/{image_id}/decision" not in paths
