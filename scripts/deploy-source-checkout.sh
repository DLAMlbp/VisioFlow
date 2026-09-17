#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash scripts/deploy-source-checkout.sh --initialize-runtime-lock
  bash scripts/deploy-source-checkout.sh [--no-pull]

The normal deployment performs a fast-forward Git pull, creates an immutable
release directory from the committed source and frontend/dist, runs migrations,
and force-recreates application containers. It never builds or pulls images.
EOF
}

mode="deploy"
pull_source=1
for argument in "$@"; do
  case "$argument" in
    --initialize-runtime-lock) mode="initialize-runtime-lock" ;;
    --no-pull) pull_source=0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $argument" >&2; usage >&2; exit 2 ;;
  esac
done

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: required command is unavailable: $1" >&2
    exit 2
  }
}

for command_name in git docker sha256sum awk cmp tar; do
  require_command "$command_name"
done

repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "ERROR: run this script inside the server Git checkout." >&2
  exit 2
}
repo_root="$(cd "$repo_root" && pwd -P)"
cd "$repo_root"

env_file="$repo_root/.env.production"
state_dir="${SOURCE_STATE_DIR:-$repo_root/.source-deploy}"
release_dir_root="$state_dir/releases"
runtime_lock="$state_dir/runtime-files.sha256"
compose_files=(-f "$repo_root/docker-compose.prod.yml" -f "$repo_root/docker-compose.source.yml")

if [[ "$state_dir" != /* ]]; then
  echo "ERROR: SOURCE_STATE_DIR must be an absolute path: $state_dir" >&2
  exit 2
fi
if [[ ! -f "$env_file" ]]; then
  echo "ERROR: production environment file is missing: $env_file" >&2
  exit 2
fi

mkdir -p "$release_dir_root" "$state_dir/backups"

runtime_files() {
  {
    printf '%s\n' Dockerfile Dockerfile.api Dockerfile.base pyproject.toml requirements-redaction.txt
    git ls-files 'models/**/manifest.json'
    git ls-files scripts/fetch_redaction_models.py scripts/verify_redaction_supply_chain.py
  } | awk 'NF && !seen[$0]++' | sort
}

render_runtime_lock() {
  local output_file="$1"
  : > "$output_file"
  while IFS= read -r file; do
    if [[ ! -f "$file" ]]; then
      echo "ERROR: runtime input is missing: $file" >&2
      rm -f "$output_file"
      exit 2
    fi
    sha256sum "$file" >> "$output_file"
  done < <(runtime_files)
}

write_runtime_lock() {
  local temporary_lock="$runtime_lock.tmp"
  render_runtime_lock "$temporary_lock"
  mv "$temporary_lock" "$runtime_lock"
}

if [[ "$mode" == "initialize-runtime-lock" ]]; then
  if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    echo "ERROR: tracked files must be clean before initializing the runtime lock." >&2
    exit 2
  fi
  write_runtime_lock
  echo "Runtime compatibility lock initialized at: $runtime_lock"
  echo "Only do this after verifying that the configured runtime images match this checkout."
  exit 0
fi

require_command curl

if [[ ! -f "$runtime_lock" ]]; then
  echo "ERROR: runtime compatibility lock is missing: $runtime_lock" >&2
  echo "Verify the base images, then run with --initialize-runtime-lock once." >&2
  exit 2
fi
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "ERROR: tracked files are modified on the server; refusing to overwrite them." >&2
  exit 2
fi

if (( pull_source )); then
  branch="${SOURCE_BRANCH:-$(git symbolic-ref --quiet --short HEAD || true)}"
  if [[ -z "$branch" ]]; then
    echo "ERROR: detached HEAD requires SOURCE_BRANCH or --no-pull." >&2
    exit 2
  fi
  echo "== Pull source: origin/$branch =="
  git pull --ff-only origin "$branch"
fi

echo "== Verify runtime compatibility =="
current_runtime_lock="$runtime_lock.current.$$"
render_runtime_lock "$current_runtime_lock"
if ! cmp --silent "$runtime_lock" "$current_runtime_lock"; then
  rm -f "$current_runtime_lock"
  echo "ERROR: runtime dependencies or model inputs changed." >&2
  echo "Build and preload new runtime images, verify them, then reinitialize the runtime lock." >&2
  exit 3
fi
rm -f "$current_runtime_lock"

release_id="$(git rev-parse HEAD)"
release_dir="$release_dir_root/$release_id"
temporary_release="$release_dir.tmp.$$"

if [[ ! -d "$release_dir" ]]; then
  echo "== Create release: $release_id =="
  rm -rf "$temporary_release"
  mkdir -p "$temporary_release"
  git archive --format=tar HEAD | tar -xf - -C "$temporary_release"

  if [[ ! -f "$temporary_release/frontend/dist/index.html" ]]; then
    rm -rf "$temporary_release"
    echo "ERROR: committed frontend/dist is missing." >&2
    echo "Build the frontend on the development machine and commit frontend/dist." >&2
    exit 3
  fi
  mv "$temporary_release" "$release_dir"
else
  echo "== Reuse release: $release_id =="
fi

export SOURCE_RELEASE_ROOT="$release_dir"
dc=(docker compose --env-file "$env_file" "${compose_files[@]}")

echo "== Validate Compose and local runtime images =="
"${dc[@]}" config --quiet
while IFS= read -r image; do
  [[ -z "$image" ]] && continue
  docker image inspect "$image" >/dev/null 2>&1 || {
    echo "ERROR: required image is not preloaded on this server: $image" >&2
    exit 4
  }
done < <("${dc[@]}" config --images | sort -u)

echo "== Start infrastructure without image pulls =="
"${dc[@]}" up -d --no-build --pull never postgres redis minio minio-init

echo "== Wait for infrastructure =="
infrastructure_ready=0
for _ in $(seq 1 60); do
  if "${dc[@]}" exec -T postgres sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
      >/dev/null 2>&1 \
    && "${dc[@]}" exec -T redis redis-cli ping >/dev/null 2>&1 \
    && "${dc[@]}" exec -T minio curl --fail --silent http://127.0.0.1:9000/minio/health/live \
      >/dev/null 2>&1; then
    infrastructure_ready=1
    break
  fi
  sleep 2
done
if (( ! infrastructure_ready )); then
  echo "ERROR: PostgreSQL, Redis, or MinIO did not become ready." >&2
  exit 5
fi

backup_file="$state_dir/backups/postgres-before-$release_id-$(date -u +%Y%m%dT%H%M%SZ).sql"
echo "== Back up database: $backup_file =="
"${dc[@]}" exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$backup_file"

echo "== Run database migrations =="
"${dc[@]}" up --no-build --pull never --force-recreate --no-deps \
  --abort-on-container-exit --exit-code-from migrate migrate

backend_services=(
  api worker-control worker-callback worker-preprocess worker-classification
  worker-beautify-plan worker-redaction worker-inpaint worker-enhance worker-render
  worker-analysis worker-openclip worker-library worker-matching worker-cleanup celery-beat
)

echo "== Recreate backend processes with mounted source =="
"${dc[@]}" up -d --no-build --pull never --force-recreate --no-deps "${backend_services[@]}"

web_port="$(awk -F= '$1 == "WEB_PORT" {print $2}' "$env_file" | tail -n 1 | tr -d '\r')"
web_port="${web_port:-8088}"
api_ready=0
for _ in $(seq 1 60); do
  if "${dc[@]}" exec -T api python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" \
    >/dev/null 2>&1; then
    api_ready=1
    break
  fi
  sleep 2
done
if (( ! api_ready )); then
  echo "ERROR: API did not become healthy; current-release was not advanced." >&2
  "${dc[@]}" logs --tail=100 api >&2 || true
  exit 5
fi

echo "== Recreate web with mounted frontend =="
"${dc[@]}" up -d --no-build --pull never --force-recreate --no-deps web

web_ready=0
for _ in $(seq 1 30); do
  if curl --fail --silent --show-error "http://127.0.0.1:$web_port/health" >/dev/null; then
    web_ready=1
    break
  fi
  sleep 2
done
if (( ! web_ready )); then
  echo "ERROR: web health check failed; current-release was not advanced." >&2
  "${dc[@]}" logs --tail=100 web >&2 || true
  exit 6
fi

printf '%s\n' "$release_dir" > "$state_dir/current-release"
printf '%s\n' "$release_id" > "$state_dir/current-commit"
echo "Deployment completed: $release_id"
echo "Mounted release: $release_dir"
