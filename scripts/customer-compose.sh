#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$project_dir/.env.production"
if [[ ! -f "$env_file" ]]; then
  printf '%s\n' 'Missing .env.production. Copy .env.production.example only if no production configuration exists, then configure credentials, domains and image versions.' >&2
  exit 2
fi
if [[ $# -eq 0 ]]; then
  printf '%s\n' 'Usage: bash scripts/customer-compose.sh <compose arguments>' 'Examples: config --quiet | ps --all | logs --tail 100 | pull | up -d' >&2
  exit 2
fi

exec docker compose --project-directory "$project_dir" --env-file "$env_file" \
  -f "$project_dir/docker-compose.prod.yml" "$@"
