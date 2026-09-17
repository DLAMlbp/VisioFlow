from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_source_compose_mounts_every_application_service_read_only() -> None:
    compose = yaml.safe_load(
        (ROOT / "docker-compose.source.yml").read_text(encoding="utf-8")
    )
    services = compose["services"]
    backend_services = {
        "api",
        "worker-control",
        "worker-callback",
        "worker-preprocess",
        "worker-classification",
        "worker-beautify-plan",
        "worker-redaction",
        "worker-inpaint",
        "worker-enhance",
        "worker-render",
        "worker-analysis",
        "worker-openclip",
        "worker-library",
        "worker-matching",
        "worker-cleanup",
        "celery-beat",
        "migrate",
    }

    assert backend_services <= services.keys()
    for service_name in backend_services:
        mounts = services[service_name]["volumes"]
        targets = {mount["target"] for mount in mounts}
        assert {
            "/app/src",
            "/app/profiles",
            "/app/assets",
            "/app/scripts",
            "/app/alembic",
        } <= targets
        assert all(mount["read_only"] is True for mount in mounts)

    assert set(services) == backend_services | {"web"}
    assert all(
        mount["read_only"] is True for mount in services["web"]["volumes"]
    )


def test_source_deploy_never_builds_or_pulls_images() -> None:
    script = (ROOT / "scripts" / "deploy-source-checkout.sh").read_text(
        encoding="utf-8"
    )

    assert "git pull --ff-only" in script
    assert "docker build" not in script
    assert "docker pull" not in script
    assert script.count("--no-build") >= 4
    assert script.count("--pull never") >= 4
    assert "--force-recreate" in script
    assert "--exit-code-from migrate" in script
    assert "pg_isready" in script
    assert "git branch --show-current" not in script
    assert "git symbolic-ref --quiet --short HEAD" in script
    assert script.index('echo "== Run database migrations =="') < script.index(
        'echo "== Recreate backend processes with mounted source =="'
    )


def test_source_deploy_guards_runtime_inputs_and_requires_committed_frontend() -> None:
    script = (ROOT / "scripts" / "deploy-source-checkout.sh").read_text(
        encoding="utf-8"
    )

    for runtime_input in (
        "pyproject.toml",
        "requirements-redaction.txt",
        "Dockerfile",
        "Dockerfile.api",
        "Dockerfile.base",
        "models/**/manifest.json",
    ):
        assert runtime_input in script
    assert 'render_runtime_lock "$current_runtime_lock"' in script
    assert 'cmp --silent "$runtime_lock" "$current_runtime_lock"' in script
    assert "require_command npm" not in script
    assert "npm ci" not in script
    assert "npm test" not in script
    assert "npm run build" not in script
    assert 'temporary_release/frontend/dist/index.html' in script
    assert "git archive --format=tar HEAD" in script


def test_frontend_distribution_is_committed_for_build_free_deployments() -> None:
    distribution = ROOT / "frontend" / "dist"

    assert (distribution / "index.html").is_file()
    assert list((distribution / "assets").glob("*.js"))
    assert list((distribution / "assets").glob("*.css"))
