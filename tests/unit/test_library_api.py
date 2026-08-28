from fastapi.testclient import TestClient

from src.core.config import Settings, get_settings
from src.main import app
from src.services.library import get_library_service


class FakeLibraryService:
    deleted_asset_id: str | None = None

    async def delete_asset(self, asset_id: str) -> None:
        self.deleted_asset_id = asset_id


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
