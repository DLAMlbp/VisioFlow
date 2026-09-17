#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

REMOTE="${DEPLOY_REMOTE:-origin}"
BRANCH="${DEPLOY_BRANCH:-main}"
COMPOSE=(docker compose -f docker-compose.yml)
APP_SERVICES=(
  web api worker-control worker-preprocess worker-classification
  worker-beautify-plan worker-redaction worker-inpaint worker-enhance
  worker-render worker-analysis worker-openclip worker-matching
  worker-cleanup celery-beat
)

exec 9>/tmp/image-intelligence-deploy.lock
flock -n 9 || { echo "Another deployment is already running." >&2; exit 1; }

command -v git >/dev/null || { echo "git is required." >&2; exit 1; }
command -v docker >/dev/null || { echo "docker is required." >&2; exit 1; }
command -v curl >/dev/null || { echo "curl is required." >&2; exit 1; }
[[ -f .env ]] || { echo ".env is missing." >&2; exit 1; }
[[ -z "$(git status --porcelain)" ]] || {
  echo "The server worktree has uncommitted changes; deployment stopped." >&2
  exit 1
}

git fetch --prune "$REMOTE" "$BRANCH"
OLD_SHA="$(git rev-parse HEAD)"
NEW_SHA="$(git rev-parse "$REMOTE/$BRANCH")"

if [[ "$OLD_SHA" == "$NEW_SHA" ]]; then
  echo "Source is already at $NEW_SHA; reconciling application services."
fi

RUNTIME_CHANGES="$(git diff --name-only "$OLD_SHA" "$NEW_SHA" -- \
  Dockerfile Dockerfile.api Dockerfile.base Dockerfile.app-update \
  pyproject.toml requirements-redaction.txt models/ docker/package-placeholder/)"
if [[ -n "$RUNTIME_CHANGES" ]]; then
  echo "Runtime dependencies changed; build and publish a new image first:" >&2
  echo "$RUNTIME_CHANGES" >&2
  exit 2
fi

rollback() {
  trap - ERR
  set +e
  echo "Deployment failed; restoring source revision $OLD_SHA" >&2
  "${COMPOSE[@]}" stop "${APP_SERVICES[@]}"
  git switch --detach "$OLD_SHA"
  "${COMPOSE[@]}" --profile tools run --rm web-builder
  "${COMPOSE[@]}" up -d --no-deps "${APP_SERVICES[@]}"
  echo "Source was restored. Database migrations were not downgraded." >&2
  exit 1
}
trap rollback ERR

"${COMPOSE[@]}" config --quiet
"${COMPOSE[@]}" stop "${APP_SERVICES[@]}"
git switch --detach "$NEW_SHA"
"${COMPOSE[@]}" config --quiet

"${COMPOSE[@]}" up -d --wait --wait-timeout 120 postgres redis minio
"${COMPOSE[@]}" run --rm --no-deps minio-init
"${COMPOSE[@]}" --profile tools run --rm web-builder
"${COMPOSE[@]}" run --rm migrate
"${COMPOSE[@]}" up -d --no-deps "${APP_SERVICES[@]}"

READY=0
for _ in {1..60}; do
  if curl --fail --silent http://127.0.0.1:18000/health/ready >/dev/null; then
    READY=1
    break
  fi
  sleep 2
done
[[ "$READY" -eq 1 ]] || { echo "API readiness check failed." >&2; false; }
curl --fail --silent http://127.0.0.1:5174/health >/dev/null

trap - ERR
echo "Deployment succeeded: $OLD_SHA -> $NEW_SHA"
