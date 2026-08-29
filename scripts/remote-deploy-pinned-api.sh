#!/usr/bin/env bash
set -euo pipefail

remote_dir="${1:?remote directory is required}"
new_api="${2:?immutable API image is required}"
web_port="${3:-8088}"
image_source="${4:-pull}"

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
backup="$deployments_dir/$deployment_id.postgres.dump"
mkdir -p "$deployments_dir"
chmod 700 "$deployments_dir"
cp --preserve=mode,ownership .env.production "$snapshot"
chmod 600 "$snapshot"

echo "== database backup =="
dc exec -T postgres sh -c 'exec pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  </dev/null > "$backup"
chmod 600 "$backup"
dc exec -T postgres sh -c 'pg_restore -l >/dev/null' < "$backup"
backup_size="$(du -h "$backup" | awk '{print $1}')"
backup_sha="$(sha256sum "$backup" | awk '{print $1}')"
echo "BACKUP_OK path=$backup size=$backup_size sha256=$backup_sha"
echo "SNAPSHOT_OK path=$snapshot"

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
  dc up -d --remove-orphans
}

set_env_value API_IMAGE "$new_api"
if [[ "$image_source" == "pull" ]]; then
  echo "== pull versioned API image =="
  if ! dc pull api worker celery-beat; then
    cp --preserve=mode,ownership "$snapshot" .env.production
    echo "PULL_FAILED configuration restored" >&2
    exit 20
  fi
else
  echo "== verify preloaded versioned API image =="
  if ! docker image inspect "$new_api" >/dev/null; then
    cp --preserve=mode,ownership "$snapshot" .env.production
    echo "LOCAL_IMAGE_MISSING configuration restored" >&2
    exit 20
  fi
fi

echo "== verify migration head in new image =="
dc run --rm --no-deps api alembic heads </dev/null

echo "== migrate database =="
if ! dc run --rm --no-deps api alembic upgrade head </dev/null; then
  cp --preserve=mode,ownership "$snapshot" .env.production
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
  exited="$(dc ps --status exited --services | grep -v '^minio-init$' || true)"
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
  dc logs --since 3m --tail 80 api worker celery-beat web >&2 || true
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
echo "DATABASE_BACKUP=$backup"
echo "API_IMAGE=$new_api"
