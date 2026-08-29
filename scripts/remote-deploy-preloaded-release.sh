#!/usr/bin/env bash
set -euo pipefail

remote_dir="${1:?remote directory is required}"
api_image="${2:?versioned API image is required}"
web_image="${3:?versioned web image is required}"
web_port="${4:-8088}"
expected_db_revision="${5:-}"

if [[ "$remote_dir" != /* ]] || [[ "$api_image" == *:latest ]] || [[ "$web_image" == *:latest ]]; then
  echo "ERROR: absolute deployment directory and non-latest images are required" >&2
  exit 2
fi

resolved_dir="$(cd "$remote_dir" && pwd -P)"
if [[ "$resolved_dir" != "$remote_dir" ]]; then
  echo "ERROR: deployment directory resolved unexpectedly" >&2
  exit 2
fi
cd "$resolved_dir"

dc() {
  docker compose --env-file .env.production -f docker-compose.prod.yml "$@"
}

database_revision() {
  dc exec -T postgres sh -c \
    'psql -U "$POSTGRES_USER" "$POSTGRES_DB" -Atc "select version_num from alembic_version"' \
    </dev/null
}

deployment_id="$(date -u +%Y%m%dT%H%M%SZ)-full"
deployments_dir="$resolved_dir/.deployments"
snapshot="$deployments_dir/$deployment_id.env.production"
backup="$deployments_dir/$deployment_id.postgres.dump"
mkdir -p "$deployments_dir"
chmod 700 "$deployments_dir"
cp --preserve=mode,ownership .env.production "$snapshot"
chmod 600 "$snapshot"

echo "== database revision before deployment =="
revision_before="$(database_revision)"
echo "$revision_before"
if [[ -n "$expected_db_revision" && "$revision_before" != "$expected_db_revision" ]]; then
  echo "ERROR: database revision differs from the expected revision" >&2
  exit 10
fi

echo "== database backup =="
dc exec -T postgres sh -c 'exec pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  </dev/null > "$backup"
chmod 600 "$backup"
dc exec -T postgres sh -c 'pg_restore -l >/dev/null' < "$backup"
echo "BACKUP_OK path=$backup size=$(du -h "$backup" | awk '{print $1}') sha256=$(sha256sum "$backup" | awk '{print $1}')"
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

restore_release() {
  echo "Restoring previous application image references..." >&2
  cp --preserve=mode,ownership "$snapshot" .env.production
  dc up -d --no-deps api worker celery-beat web </dev/null
}

docker image inspect "$api_image" >/dev/null
docker image inspect "$web_image" >/dev/null
set_env_value API_IMAGE "$api_image"
set_env_value WEB_IMAGE "$web_image"

echo "== start full application release =="
if ! dc up -d --no-deps api worker celery-beat web </dev/null; then
  restore_release
  echo "START_FAILED previous application release restored" >&2
  exit 20
fi

healthy=0
for attempt in $(seq 1 18); do
  web_ok=0
  api_ok=0
  workers_ok=0
  curl -fsS --max-time 5 "http://127.0.0.1:$web_port/" >/dev/null && web_ok=1 || true
  [[ "$(docker inspect --format '{{.State.Health.Status}}' image-intelligence-api-1 2>/dev/null || true)" == "healthy" ]] \
    && api_ok=1 || true
  running="$(dc ps --status running --services)"
  if grep -qx worker <<<"$running" && grep -qx celery-beat <<<"$running"; then workers_ok=1; fi
  if (( web_ok == 1 && api_ok == 1 && workers_ok == 1 )); then
    healthy=1
    break
  fi
  echo "health attempt $attempt/18 pending"
  sleep 5
done

if (( healthy != 1 )); then
  echo "HEALTH_FAILED limited logs follow" >&2
  dc ps >&2 || true
  dc logs --since 3m --tail 100 api worker celery-beat web >&2 || true
  restore_release
  sleep 5
  echo "Previous application release restored" >&2
  exit 21
fi

revision_after="$(database_revision)"
if [[ "$revision_after" != "$revision_before" ]]; then
  echo "ERROR: database revision changed unexpectedly; restoring previous application" >&2
  restore_release
  exit 22
fi

echo "== deployed compose status =="
dc ps
echo "DATABASE_REVISION=$revision_after"
echo "DEPLOYMENT_OK id=$deployment_id"
echo "SNAPSHOT=$snapshot"
echo "DATABASE_BACKUP=$backup"
echo "API_IMAGE=$api_image"
echo "WEB_IMAGE=$web_image"
