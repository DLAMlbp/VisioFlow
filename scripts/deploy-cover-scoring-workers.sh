#!/usr/bin/env bash
set -euo pipefail

remote_dir="${1:?remote directory is required}"
release_image="${2:?immutable release image is required}"
compose_candidate="${3:-}"

if [[ "$remote_dir" != /* ]]; then
  echo "ERROR: remote directory must be absolute" >&2
  exit 2
fi
if [[ "$release_image" != sha256:* && "$release_image" != *@sha256:* ]]; then
  echo "ERROR: release image must use an immutable digest" >&2
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

deployment_id="$(date -u +%Y%m%dT%H%M%SZ)-cover-scoring"
deployments_dir="$resolved_dir/.deployments"
snapshot="$deployments_dir/$deployment_id.env.production"
compose_snapshot="$deployments_dir/$deployment_id.docker-compose.prod.yml"
mkdir -p "$deployments_dir"
chmod 700 "$deployments_dir"
cp --preserve=mode,ownership .env.production "$snapshot"
cp --preserve=mode,ownership docker-compose.prod.yml "$compose_snapshot"
chmod 600 "$snapshot" "$compose_snapshot"

before_other="$(mktemp)"
after_other="$(mktemp)"
release_committed=0

other_container_ids() {
  while read -r service; do
    [[ "$service" == "worker-classification" || "$service" == "worker-render" ]] && continue
    container_id="$(dc ps -q "$service")"
    [[ -n "$container_id" ]] && printf '%s=%s\n' "$service" "$container_id"
  done < <(dc config --services)
}

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

restore_on_error() {
  exit_code="$?"
  if (( exit_code != 0 && release_committed == 0 )); then
    cp --preserve=mode,ownership "$snapshot" .env.production
    cp --preserve=mode,ownership "$compose_snapshot" docker-compose.prod.yml
    dc up -d --no-deps worker-classification worker-render >/dev/null || true
  fi
  rm -f "$before_other" "$after_other"
  trap - EXIT
  exit "$exit_code"
}
trap restore_on_error EXIT

docker image inspect "$release_image" >/dev/null
if [[ -n "$compose_candidate" ]]; then
  if [[ "$compose_candidate" != /* || ! -f "$compose_candidate" ]]; then
    echo "ERROR: compose candidate must be an existing absolute file" >&2
    exit 2
  fi
  if ! docker compose --env-file .env.production -f "$compose_candidate" config --quiet; then
    echo "ERROR: compose candidate is invalid" >&2
    exit 2
  fi
  temporary_compose="$(mktemp "$resolved_dir/docker-compose.prod.yml.XXXXXX")"
  cp "$compose_candidate" "$temporary_compose"
  chmod --reference=docker-compose.prod.yml "$temporary_compose"
  chown --reference=docker-compose.prod.yml "$temporary_compose"
  mv "$temporary_compose" docker-compose.prod.yml
  rm -f "$compose_candidate"
fi
dc config --quiet
other_container_ids | sort > "$before_other"

set_env_value CLASSIFICATION_IMAGE "$release_image"
set_env_value RENDER_IMAGE "$release_image"
dc config --quiet

# Classification writes the semantic assessment before render consumes it.
dc up -d --no-deps worker-classification
dc up -d --no-deps worker-render

for service in worker-classification worker-render; do
  healthy=0
  for _attempt in $(seq 1 30); do
    container_id="$(dc ps -q "$service")"
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id")"
    if [[ "$health" == "healthy" ]]; then
      healthy=1
      break
    fi
    sleep 2
  done
  if (( healthy != 1 )); then
    echo "ERROR: $service did not become healthy" >&2
    dc logs --since 3m --tail 100 "$service" >&2 || true
    exit 3
  fi

  actual_image_id="$(docker inspect --format '{{.Image}}' "$(dc ps -q "$service")")"
  expected_image_id="$(docker image inspect --format '{{.Id}}' "$release_image")"
  if [[ "$actual_image_id" != "$expected_image_id" ]]; then
    echo "ERROR: $service is not running the requested image" >&2
    exit 4
  fi
done

other_container_ids | sort > "$after_other"
if ! cmp -s "$before_other" "$after_other"; then
  echo "ERROR: a non-scoring service container changed during deployment" >&2
  diff -u "$before_other" "$after_other" >&2 || true
  exit 5
fi

for service in worker-classification worker-render; do
  container_id="$(dc ps -q "$service")"
  restarts="$(docker inspect --format '{{.RestartCount}}' "$container_id")"
  if [[ "$restarts" != "0" ]]; then
    echo "ERROR: $service restarted unexpectedly" >&2
    exit 6
  fi
done

release_committed=1
rm -f "$before_other" "$after_other"
trap - EXIT
echo "COVER_SCORING_DEPLOYMENT_OK id=$deployment_id"
echo "RELEASE_IMAGE=$release_image"
echo "SNAPSHOT=$snapshot"
echo "COMPOSE_SNAPSHOT=$compose_snapshot"
