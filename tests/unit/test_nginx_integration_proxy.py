from pathlib import Path

import yaml


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


def test_browser_api_proxy_does_not_inject_the_service_api_key() -> None:
    config = Path("frontend/nginx.conf.template").read_text(encoding="utf-8")
    browser_location = config.split("location /api/ {", 1)[1].split("}", 1)[0]

    assert "proxy_set_header X-API-Key" not in browser_location
    assert "${API_KEY}" not in config


def test_web_containers_do_not_receive_backend_secrets() -> None:
    for compose_name in ("docker-compose.yml", "docker-compose.prod.yml"):
        compose = yaml.safe_load(Path(compose_name).read_text(encoding="utf-8"))
        assert "env_file" not in compose["services"]["web"]


def test_https_forwarding_is_preserved_for_secure_session_cookies() -> None:
    config = Path("frontend/nginx.conf.template").read_text(encoding="utf-8")

    assert "map $http_x_forwarded_proto $upstream_forwarded_proto" in config
    assert "proxy_set_header X-Forwarded-Proto $upstream_forwarded_proto;" in config
