from pathlib import Path


def test_integration_proxy_preserves_company_api_key() -> None:
    config = Path("frontend/nginx.conf.template").read_text(encoding="utf-8")
    integration_location = config.split("location /api/v1/integration/", 1)[1].split("location /api/", 1)[0]

    assert "proxy_set_header X-API-Key $http_x_api_key;" in integration_location
    assert 'proxy_set_header X-API-Key "${API_KEY}";' not in integration_location


def test_partner_legacy_path_preserves_integration_api_key() -> None:
    config = Path("frontend/nginx.conf.template").read_text(encoding="utf-8")
    partner_location = config.split(
        "location = /api/app/image/filter-requests", 1
    )[1].split("location /api/", 1)[0]

    assert "proxy_set_header X-API-Key $http_x_api_key;" in partner_location
    assert 'proxy_set_header X-API-Key "${API_KEY}";' not in partner_location
