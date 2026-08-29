#!/usr/bin/env bash
set -euo pipefail

remote_dir="${1:?remote directory is required}"
new_port="${2:?new web port is required}"
public_origin="${3:?public origin is required}"

resolved_dir="$(cd "$remote_dir" && pwd -P)"
if [[ "$resolved_dir" != "$remote_dir" ]] || [[ ! "$new_port" =~ ^[0-9]+$ ]]; then
  echo "ERROR: invalid deployment directory or port" >&2
  exit 2
fi
cd "$resolved_dir"

dc() {
  docker compose --env-file .env.production -f docker-compose.prod.yml "$@"
}

deployment_id="$(date -u +%Y%m%dT%H%M%SZ)-port-$new_port"
deployments_dir="$resolved_dir/.deployments"
snapshot="$deployments_dir/$deployment_id.env.production"
mkdir -p "$deployments_dir"
chmod 700 "$deployments_dir"
cp --preserve=mode,ownership .env.production "$snapshot"
chmod 600 "$snapshot"

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

restore_port() {
  cp --preserve=mode,ownership "$snapshot" .env.production
  dc up -d --no-deps api web minio </dev/null
}

set_env_value WEB_PORT "$new_port"
set_env_value CORS_ALLOWED_ORIGINS "$public_origin"
set_env_value MINIO_API_CORS_ALLOW_ORIGIN "$public_origin"

if ! dc up -d --no-deps api web minio </dev/null; then
  restore_port
  echo "PORT_SWITCH_FAILED previous configuration restored" >&2
  exit 30
fi

healthy=0
for attempt in $(seq 1 18); do
  web_ok=0
  api_ok=0
  curl -fsS --max-time 5 "http://127.0.0.1:$new_port/" >/dev/null && web_ok=1 || true
  [[ "$(docker inspect --format '{{.State.Health.Status}}' image-intelligence-api-1)" == "healthy" ]] \
    && api_ok=1 || true
  if (( web_ok == 1 && api_ok == 1 )); then
    healthy=1
    break
  fi
  echo "port health attempt $attempt/18 pending"
  sleep 5
done

if (( healthy != 1 )); then
  dc ps >&2 || true
  dc logs --since 3m --tail 80 api web minio >&2 || true
  restore_port
  echo "PORT_HEALTH_FAILED previous configuration restored" >&2
  exit 31
fi

dc ps
echo "PORT_SWITCH_OK port=$new_port origin=$public_origin"
echo "PORT_SNAPSHOT=$snapshot"
