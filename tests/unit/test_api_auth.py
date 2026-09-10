from fastapi.testclient import TestClient

from src.core.config import Settings, get_settings
from src.main import app


def test_api_requires_login_when_api_key_is_not_configured() -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(api_key="")
    client = TestClient(app)

    response = client.post("/api/v1/uploads/presign", json={})

    app.dependency_overrides.clear()
    assert response.status_code == 401
    assert response.json()["detail"] == "请先登录"


def test_api_rejects_invalid_api_key_before_validating_request_body() -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(api_key="expected-key")
    client = TestClient(app)

    response = client.post("/api/v1/uploads/presign", json={}, headers={"X-API-Key": "wrong-key"})

    app.dependency_overrides.clear()
    assert response.status_code == 401


def test_health_check_does_not_require_api_key() -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200


def test_response_contains_request_id() -> None:
    client = TestClient(app)

    response = client.get("/health", headers={"X-Request-ID": "trace-test-001"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "trace-test-001"
