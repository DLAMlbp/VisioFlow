#!/usr/bin/env bash
set -euo pipefail

remote_dir="${1:?remote directory is required}"
new_api="${2:?immutable API image is required}"
web_port="${3:-8088}"
image_source="${4:-pull}"
compose_candidate="${5:-}"
new_web="${6:-}"

if [[ "$remote_dir" != /* ]] || [[ "$new_api" == *:latest ]] || [[ "$new_api" != *:* ]]; then
  echo "ERROR: absolute remote directory and a non-latest versioned API image are required" >&2
  exit 2
fi
if [[ "$image_source" != "pull" && "$image_source" != "local" ]]; then
  echo "ERROR: image source must be pull or local" >&2
  exit 2
fi

resolved_dir="$(cd "$remote_dir" && pwd -P)"
if [[ "$resolved_dir" != "$remote_dir" ]]; then
  echo "ERROR: remote directory resolved unexpectedly" >&2
  exit 2
fi
cd "$resolved_dir"

dc() {
  docker compose --env-file .env.production -f docker-compose.prod.yml "$@"
}

deployment_id="$(date -u +%Y%m%dT%H%M%SZ)-api"
deployments_dir="$resolved_dir/.deployments"
snapshot="$deployments_dir/$deployment_id.env.production"
compose_snapshot="$deployments_dir/$deployment_id.docker-compose.prod.yml"
backup="$deployments_dir/$deployment_id.postgres.dump"
mkdir -p "$deployments_dir"
chmod 700 "$deployments_dir"
cp --preserve=mode,ownership .env.production "$snapshot"
cp --preserve=mode,ownership docker-compose.prod.yml "$compose_snapshot"
chmod 600 "$snapshot"
chmod 600 "$compose_snapshot"
compose_temporary=""
release_committed=0

restore_config_on_error() {
  local exit_code="$?"
  if (( exit_code != 0 && release_committed == 0 )); then
    cp --preserve=mode,ownership "$snapshot" .env.production
    cp --preserve=mode,ownership "$compose_snapshot" docker-compose.prod.yml
  fi
  trap - EXIT
  exit "$exit_code"
}
trap restore_config_on_error EXIT

if [[ -n "$compose_candidate" ]]; then
  if [[ "$compose_candidate" != /* ]] || [[ ! -f "$compose_candidate" ]]; then
    echo "ERROR: compose candidate must be an existing absolute file" >&2
    exit 2
  fi
  compose_temporary="$(mktemp "$resolved_dir/docker-compose.prod.yml.XXXXXX")"
  cp "$compose_candidate" "$compose_temporary"
  chmod --reference=docker-compose.prod.yml "$compose_temporary"
  chown --reference=docker-compose.prod.yml "$compose_temporary"
  if ! docker compose --env-file .env.production -f "$compose_temporary" config --quiet; then
    rm -f "$compose_temporary"
    echo "ERROR: compose candidate is invalid" >&2
    exit 2
  fi
fi
if [[ -n "$new_web" ]] && ([[ "$new_web" == *:latest ]] || [[ "$new_web" != *:* ]]); then
  echo "ERROR: web image must be a non-latest versioned image" >&2
  exit 2
fi

echo "== database backup =="
dc exec -T postgres sh -c 'exec pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  </dev/null > "$backup"
chmod 600 "$backup"
dc exec -T postgres sh -c 'pg_restore -l >/dev/null' < "$backup"
backup_size="$(du -h "$backup" | awk '{print $1}')"
backup_sha="$(sha256sum "$backup" | awk '{print $1}')"
echo "BACKUP_OK path=$backup size=$backup_size sha256=$backup_sha"
echo "SNAPSHOT_OK path=$snapshot"
echo "COMPOSE_SNAPSHOT_OK path=$compose_snapshot"

if [[ -n "$compose_temporary" ]]; then
  mv "$compose_temporary" docker-compose.prod.yml
fi

set_env_value() {
  local key="$1" value="$2" source='.env.production' temporary
  temporary="$(mktemp "$resolved_dir/.env.production.XXXXXX")"
  awk -v key="$key" -v value="$value" '
    BEGIN { replaced=0 }
    index($0,key "=")==1 { print key "=" value; replaced=1; next }
    { print }
    END { if (!replaced) print key "=" value }
  ' "$source" > "$temporary"
  chmod --reference="$source" "$temporary"
  chown --reference="$source" "$temporary"
  mv "$temporary" "$source"
}

restore_app() {
  echo "Restoring previous application image references..." >&2
  cp --preserve=mode,ownership "$snapshot" .env.production
  cp --preserve=mode,ownership "$compose_snapshot" docker-compose.prod.yml
  dc up -d --remove-orphans
}

set_env_value API_IMAGE "$new_api"
set_env_value API_GATEWAY_IMAGE "$new_api"
set_env_value PIPELINE_AI_TIMEOUT_SECONDS "300"
set_env_value PIPELINE_INPAINT_TIMEOUT_SECONDS "180"
set_env_value PIPELINE_JOB_TIMEOUT_PER_IMAGE_SECONDS "30"
set_env_value PIPELINE_ENHANCEMENT_MAX_RECOVERY_ATTEMPTS "0"
set_env_value INTEGRATION_RATE_LIMIT_BURST_IMAGES "20"
if [[ -n "$new_web" ]]; then
  set_env_value WEB_IMAGE "$new_web"
fi
if [[ "$image_source" == "pull" ]]; then
  echo "== pull versioned API image =="
  if ! docker pull "$new_api" || { [[ -n "$new_web" ]] && ! docker pull "$new_web"; }; then
    cp --preserve=mode,ownership "$snapshot" .env.production
    cp --preserve=mode,ownership "$compose_snapshot" docker-compose.prod.yml
    echo "PULL_FAILED configuration restored" >&2
    exit 20
  fi
else
  echo "== verify preloaded versioned API image =="
  if ! docker image inspect "$new_api" >/dev/null; then
    cp --preserve=mode,ownership "$snapshot" .env.production
    cp --preserve=mode,ownership "$compose_snapshot" docker-compose.prod.yml
    echo "LOCAL_IMAGE_MISSING configuration restored" >&2
    exit 20
  fi
  if [[ -n "$new_web" ]] && ! docker image inspect "$new_web" >/dev/null; then
    echo "LOCAL_WEB_IMAGE_MISSING configuration restored" >&2
    exit 20
  fi
fi

echo "== verify migration head in new image =="
dc run --rm --no-deps api alembic heads </dev/null

echo "== migrate database =="
if ! dc run --rm --no-deps api alembic upgrade head </dev/null; then
  cp --preserve=mode,ownership "$snapshot" .env.production
  cp --preserve=mode,ownership "$compose_snapshot" docker-compose.prod.yml
  echo "MIGRATION_FAILED configuration restored; application was not replaced" >&2
  exit 21
fi
echo "== migration revision =="
dc run --rm --no-deps api alembic current </dev/null

echo "== start release =="
if ! dc up -d --remove-orphans; then
  restore_app
  echo "START_FAILED previous application image restored; database remains forward-migrated" >&2
  exit 22
fi

healthy=0
for attempt in $(seq 1 18); do
  web_ok=0
  api_ok=0
  exited="$(dc ps --status exited --services | grep -vE '^(minio-init|migrate)$' || true)"
  restarting="$(dc ps --status restarting --services 2>/dev/null || true)"
  curl -fsS --max-time 5 "http://127.0.0.1:$web_port/" >/dev/null && web_ok=1 || true
  dc exec -T api python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=5)" \
    </dev/null >/dev/null 2>&1 && api_ok=1 || true
  if (( web_ok == 1 && api_ok == 1 )) && [[ -z "$exited" && -z "$restarting" ]]; then
    healthy=1
    break
  fi
  echo "health attempt $attempt/18 pending"
  sleep 5
done

if (( healthy != 1 )); then
  echo "HEALTH_FAILED limited logs follow" >&2
  dc ps >&2 || true
  dc logs --since 3m --tail 80 api worker-render worker-cleanup celery-beat web >&2 || true
  restore_app
  sleep 5
  echo "Previous application image restored; database remains forward-migrated." >&2
  exit 23
fi

echo "== deployed compose status =="
dc ps
echo "== final migration revision =="
dc exec -T api alembic current </dev/null
echo "DEPLOYMENT_OK id=$deployment_id"
echo "SNAPSHOT=$snapshot"
echo "COMPOSE_SNAPSHOT=$compose_snapshot"
echo "DATABASE_BACKUP=$backup"
echo "API_IMAGE=$new_api"
if [[ -n "$new_web" ]]; then echo "WEB_IMAGE=$new_web"; fi
release_committed=1
trap - EXIT
