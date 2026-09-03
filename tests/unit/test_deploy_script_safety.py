from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_production_compose_uses_the_consolidated_worker_topology() -> None:
    compose = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")

    assert "worker-openclip:" in compose
    assert "-Q openclip" in compose
    assert "-Q classification,filtering" in compose
    assert "worker-embedding:" not in compose
    assert "worker-library:" not in compose
    assert "worker-filter:" not in compose
    assert "worker-vision:" not in compose
    assert "-Q classification,filtering -l info --concurrency=4" in compose


def test_worker_healthcheck_does_not_use_celery_pidbox_or_reload_models() -> None:
    compose = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")

    assert "inspect ping" not in compose
    assert "redis.Redis.from_url" in compose
    assert "os.environ['REDIS_URL']" in compose
    assert compose.count("--without-mingle --without-gossip") == 13


def test_deploy_script_versions_and_rolls_back_the_compose_file() -> None:
    script = (ROOT / "scripts" / "deploy-production.ps1").read_text(encoding="utf-8")

    assert "compose_sha256" in script
    assert 'sha256sum "$compose_temporary"' in script
    assert 'cp --preserve=mode,ownership docker-compose.prod.yml "$rollback_compose"' in script
    assert 'cp --preserve=mode,ownership "$rollback_compose" docker-compose.prod.yml' in script
    assert "$composePayload = [Convert]::ToBase64String" in script


def test_legacy_worker_retirement_is_explicit_and_idle_only() -> None:
    script = (ROOT / "scripts" / "deploy-production.ps1").read_text(encoding="utf-8")

    assert "[switch]$RetireLegacyWorkers" in script
    assert "active_jobs" in script
    for queue in ("embedding", "library", "filtering", "vision"):
        assert queue in script
    assert 'if [[ "$transition_mode" != "execute" ]]' in script
    assert '"${compose[@]}" stop --timeout 30 "${services_to_stop[@]}"' in script
    assert "Restore-LegacyWorkers" in script
    assert "including stopping the legacy topology" in script


def test_containerized_release_tests_mount_the_production_compose() -> None:
    script = (ROOT / "scripts" / "deploy-production.ps1").read_text(encoding="utf-8")

    assert '$productionComposePath = Join-Path $repoRoot "docker-compose.prod.yml"' in script
    assert '"${productionComposePath}:/app/docker-compose.prod.yml:ro"' in script
    assert '$dockerfilePath = Join-Path $repoRoot "Dockerfile"' in script
    assert '"${dockerfilePath}:/app/Dockerfile:ro"' in script


def test_compose_is_validated_before_legacy_workers_are_stopped() -> None:
    script = (ROOT / "scripts" / "deploy-production.ps1").read_text(encoding="utf-8")

    validation = script.index('Assert-ExitCode "Validate production compose"')
    retirement = script.index('Write-Host "`n== Retire verified-idle legacy workers =="')
    assert validation < retirement
    assert "$savedImageEnvironment" in script
    assert "$env:API_GATEWAY_IMAGE = $apiPinnedImage" in script


def test_dockerfile_does_not_duplicate_the_large_model_cache_for_chown() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "chown -R app:app /app /opt/model-cache" not in dockerfile
    assert "USER app" in dockerfile


def test_prebuilt_api_release_requires_exact_repository_digest_and_skips_build() -> None:
    script = (ROOT / "scripts" / "deploy-production.ps1").read_text(encoding="utf-8")

    assert '[string]$PrebuiltApiImage = ""' in script
    assert "$PrebuiltApiImage -and -not $deployApi" in script
    assert '[Regex]::Escape("$apiRepository@sha256:")' in script
    assert '"^${expectedDigestPrefix}[0-9a-f]{64}$"' in script
    assert "$apiPinnedImage = $PrebuiltApiImage" in script
    prebuilt_branch = script.index("if ($PrebuiltApiImage) {", script.index("if ($deployApi) {"))
    normal_build_branch = script.index("else {", prebuilt_branch)
    docker_build = script.index("& docker build --pull --tag $apiImage .")
    assert prebuilt_branch < normal_build_branch < docker_build


def test_api_deploy_applies_pipeline_recovery_runtime_settings() -> None:
    script = (ROOT / "scripts" / "deploy-production.ps1").read_text(encoding="utf-8")

    assert 'set_env_value PIPELINE_AI_TIMEOUT_SECONDS "300"' in script
    assert 'set_env_value PIPELINE_INPAINT_TIMEOUT_SECONDS "180"' in script
    assert 'set_env_value PIPELINE_JOB_TIMEOUT_PER_IMAGE_SECONDS "30"' in script
    assert 'set_env_value PIPELINE_ENHANCEMENT_MAX_RECOVERY_ATTEMPTS "0"' in script
    assert 'set_env_value INTEGRATION_RATE_LIMIT_BURST_IMAGES "20"' in script


def test_local_api_deploy_updates_gateway_and_rolls_back_compose() -> None:
    script = (ROOT / "scripts" / "remote-deploy-pinned-api.sh").read_text(
        encoding="utf-8"
    )

    assert 'set_env_value API_GATEWAY_IMAGE "$new_api"' in script
    assert 'docker image inspect "$new_api"' in script
    assert 'cp --preserve=mode,ownership docker-compose.prod.yml "$compose_snapshot"' in script
    assert 'cp --preserve=mode,ownership "$compose_snapshot" docker-compose.prod.yml' in script
    assert 'docker compose --env-file .env.production -f "$compose_temporary" config --quiet' in script
    assert 'set_env_value PIPELINE_ENHANCEMENT_MAX_RECOVERY_ATTEMPTS "0"' in script
    assert 'set_env_value INTEGRATION_RATE_LIMIT_BURST_IMAGES "20"' in script
    assert "trap restore_config_on_error EXIT" in script
    assert "grep -vE '^(minio-init|migrate)$'" in script
    backup = script.index('echo "== database backup =="')
    replace_compose = script.index('mv "$compose_temporary" docker-compose.prod.yml')
    assert backup < replace_compose
