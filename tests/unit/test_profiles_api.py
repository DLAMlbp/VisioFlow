from fastapi.testclient import TestClient

from src.core.config import Settings, get_settings
from src.main import app


def test_list_profiles_returns_configured_filter_profile() -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        api_key="test-api-key",
        profiles_directory="profiles",
    )
    client = TestClient(app)

    response = client.get("/api/v1/filter-profiles", headers={"X-API-Key": "test-api-key"})

    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()[0]["id"] == "renovation_submission_v1"
    assert "批量去重" in response.json()[0]["description"]


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
    assert [profile["id"] for profile in response.json()] == [
        "library_similarity_v1",
        "library_similarity_v2",
    ]
