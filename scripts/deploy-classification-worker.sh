#!/usr/bin/env bash
set -euo pipefail

remote_dir="${1:?remote directory is required}"
classification_image="${2:?immutable classification image is required}"

if [[ "$remote_dir" != /* ]]; then
  echo "ERROR: remote directory must be absolute" >&2
  exit 2
fi
if [[ "$classification_image" != sha256:* && "$classification_image" != *@sha256:* ]]; then
  echo "ERROR: classification image must use an immutable digest" >&2
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

deployment_id="$(date -u +%Y%m%dT%H%M%SZ)-classification"
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
    [[ "$service" == "worker-classification" ]] && continue
    container_id="$(dc ps -q "$service")"
    [[ -n "$container_id" ]] && printf '%s=%s\n' "$service" "$container_id"
  done < <(dc config --services)
}

restore_on_error() {
  exit_code="$?"
  if (( exit_code != 0 && release_committed == 0 )); then
    cp --preserve=mode,ownership "$snapshot" .env.production
    dc up -d --no-deps worker-classification >/dev/null || true
  fi
  rm -f "$before_other" "$after_other"
  trap - EXIT
  exit "$exit_code"
}
trap restore_on_error EXIT

docker image inspect "$classification_image" >/dev/null
dc config --quiet
other_container_ids | sort > "$before_other"

temporary="$(mktemp "$resolved_dir/.env.production.XXXXXX")"
awk -v value="$classification_image" '
  BEGIN { replaced=0 }
  index($0,"CLASSIFICATION_IMAGE=")==1 {
    print "CLASSIFICATION_IMAGE=" value
    replaced=1
    next
  }
  { print }
  END { if (!replaced) print "CLASSIFICATION_IMAGE=" value }
' .env.production > "$temporary"
chmod --reference=.env.production "$temporary"
chown --reference=.env.production "$temporary"
mv "$temporary" .env.production

dc config --quiet
dc up -d --no-deps worker-classification

healthy=0
for _attempt in $(seq 1 30); do
  container_id="$(dc ps -q worker-classification)"
  health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id")"
  if [[ "$health" == "healthy" ]]; then
    healthy=1
    break
  fi
  sleep 2
done
if (( healthy != 1 )); then
  echo "ERROR: classification worker did not become healthy" >&2
  dc logs --since 3m --tail 100 worker-classification >&2 || true
  exit 3
fi

actual_image_id="$(docker inspect --format '{{.Image}}' "$(dc ps -q worker-classification)")"
expected_image_id="$(docker image inspect --format '{{.Id}}' "$classification_image")"
if [[ "$actual_image_id" != "$expected_image_id" ]]; then
  echo "ERROR: classification worker is not running the requested image" >&2
  exit 4
fi

other_container_ids | sort > "$after_other"
if ! cmp -s "$before_other" "$after_other"; then
  echo "ERROR: a non-classification service container changed during deployment" >&2
  diff -u "$before_other" "$after_other" >&2 || true
  exit 5
fi

release_committed=1
rm -f "$before_other" "$after_other"
trap - EXIT
echo "CLASSIFICATION_DEPLOYMENT_OK id=$deployment_id"
echo "CLASSIFICATION_IMAGE=$classification_image"
echo "SNAPSHOT=$snapshot"
echo "COMPOSE_SNAPSHOT=$compose_snapshot"
