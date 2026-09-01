[CmdletBinding()]
param(
    [ValidateSet("All", "Api", "Web")]
    [string]$Component = "All",

    [string]$Server = "47.111.188.85",
    [int]$SshPort = 22,
    [string]$RemoteUser = "root",
    [string]$RemoteDirectory = "/opt/image-intelligence",
    [string]$IdentityFile = "",
    [string]$ImageNamespace = "ghcr.io/zuixi01",
    [int]$WebPort = 8088,

    [switch]$RequireCleanGit,
    [switch]$SkipTests,
    [switch]$BuildOnly,
    [switch]$RetireLegacyWorkers,
    [string]$PrebuiltApiImage = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert-Command {
    param([Parameter(Mandatory)][string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command is unavailable: $Name"
    }
}

function Assert-ExitCode {
    param([Parameter(Mandatory)][string]$Operation)

    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed with exit code $LASTEXITCODE."
    }
}

function ConvertTo-BashLiteral {
    param([Parameter(Mandatory)][string]$Value)

    $singleQuoteEscape = "'`"'`"'"
    return "'" + $Value.Replace("'", $singleQuoteEscape) + "'"
}

function Invoke-RemoteBash {
    param(
        [Parameter(Mandatory)][string]$Script,
        [string[]]$Arguments = @()
    )

    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Script))
    $remoteArguments = ($Arguments | ForEach-Object { ConvertTo-BashLiteral $_ }) -join " "
    $remoteCommand = "printf '%s' '$encoded' | base64 -d | bash -s -- $remoteArguments"
    & ssh @script:SshArguments $script:RemoteTarget $remoteCommand
    Assert-ExitCode "Remote command"
}

function Test-RemoteImageExists {
    param([Parameter(Mandatory)][string]$Image)

    $previousErrorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $inspectOutput = & docker buildx imagetools inspect $Image 2>&1
        $inspectExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorPreference
    }
    if ($inspectExitCode -eq 0) {
        return $true
    }
    $message = $inspectOutput -join "`n"
    if ($message -match '(?i)manifest unknown|not found|no such manifest') {
        return $false
    }
    throw "Unable to verify whether immutable image already exists: $Image`n$message"
}

function Get-PinnedImageReference {
    param(
        [Parameter(Mandatory)][string]$Image,
        [Parameter(Mandatory)][string]$Repository
    )

    $previousErrorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $manifestOutput = & docker buildx imagetools inspect $Image --format "{{json .Manifest}}" 2>&1
        $manifestExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorPreference
    }
    if ($manifestExitCode -ne 0) {
        throw "Unable to resolve the pushed image digest: $Image`n$($manifestOutput -join "`n")"
    }
    try {
        $manifest = ($manifestOutput -join "`n") | ConvertFrom-Json
        $digest = [string]$manifest.digest
    }
    catch {
        throw "Registry returned an invalid manifest for $Image. $($_.Exception.Message)"
    }
    if ($digest -notmatch '^sha256:[0-9a-f]{64}$') {
        throw "Registry did not return a valid immutable digest for $Image."
    }
    return "$Repository@$digest"
}

function Invoke-ServerPrecheck {
    Write-Host "`n== Server safety precheck ==" -ForegroundColor Cyan
    Invoke-RemoteBash -Script $script:PrecheckScript -Arguments @($RemoteDirectory)
}

function Restore-LegacyWorkers {
    if (-not $RetireLegacyWorkers) {
        return
    }
    Write-Warning "Restoring legacy workers because the topology transition did not complete."
    Invoke-RemoteBash -Script $script:RestoreLegacyWorkersScript -Arguments @($RemoteDirectory)
}

$PrecheckScript = @'
set -euo pipefail

remote_dir="$1"
if [[ "$remote_dir" != /* ]]; then
  echo "ERROR: remote directory must be absolute" >&2
  exit 2
fi
if [[ ! -d "$remote_dir" ]]; then
  echo "ERROR: remote directory does not exist: $remote_dir" >&2
  exit 2
fi
resolved_dir="$(cd "$remote_dir" && pwd -P)"
if [[ "$resolved_dir" != "$remote_dir" ]]; then
  echo "ERROR: remote directory resolved to an unexpected path: $resolved_dir" >&2
  exit 2
fi
if [[ ! -f "$resolved_dir/docker-compose.prod.yml" || ! -f "$resolved_dir/.env.production" ]]; then
  echo "ERROR: production compose or environment file is missing" >&2
  exit 2
fi

echo "-- uptime"
uptime
echo "-- memory"
free -h
echo "-- swap"
swapon --show
echo "-- filesystems"
df -h
echo "-- inodes"
df -ih
echo "-- docker usage"
docker system df
echo "-- compose status"
(
  cd "$resolved_dir"
  docker compose --env-file .env.production -f docker-compose.prod.yml ps
)

issues=()

check_filesystem() {
  local label="$1"
  local path="$2"
  local line total_kb available_kb use_percent required_kb twenty_percent_kb inode_percent
  line="$(df -Pk "$path" | awk 'NR==2')"
  total_kb="$(awk '{print $2}' <<<"$line")"
  available_kb="$(awk '{print $4}' <<<"$line")"
  use_percent="$(awk '{gsub(/%/, "", $5); print $5}' <<<"$line")"
  twenty_percent_kb=$((total_kb / 5))
  required_kb=10485760
  if (( twenty_percent_kb > required_kb )); then required_kb="$twenty_percent_kb"; fi
  if (( use_percent >= 85 )); then issues+=("$label usage is ${use_percent}% (limit: <85%)"); fi
  if (( available_kb < required_kb )); then
    issues+=("$label free space is below the stricter of 10 GiB or 20%")
  fi
  inode_percent="$(df -Pi "$path" | awk 'NR==2 {gsub(/%/, "", $5); print $5}')"
  if (( inode_percent >= 85 )); then issues+=("$label inode usage is ${inode_percent}% (limit: <85%)"); fi
}

check_filesystem "root filesystem" "/"
docker_root="$(docker info --format '{{.DockerRootDir}}')"
check_filesystem "Docker filesystem" "$docker_root"
check_filesystem "target filesystem" "$resolved_dir"

mem_total_kb="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
mem_available_kb="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
required_mem_kb=$((mem_total_kb / 4))
if (( required_mem_kb < 2097152 )); then required_mem_kb=2097152; fi
if (( mem_available_kb < required_mem_kb )); then
  issues+=("MemAvailable is below the stricter of 2 GiB or 25% of total memory")
fi

cpu_count="$(getconf _NPROCESSORS_ONLN)"
load_five="$(awk '{print $2}' /proc/loadavg)"
if awk -v load="$load_five" -v cpus="$cpu_count" 'BEGIN {exit !(load > cpus)}'; then
  issues+=("5-minute load average $load_five is higher than $cpu_count CPU cores")
fi

busy_tasks="$(pgrep -af 'docker[[:space:]]+(build|buildx)|npm[[:space:]]+run[[:space:]]+build|pnpm[[:space:]]+build|yarn[[:space:]]+build|mvn[[:space:]]+package|gradle[[:space:]]+build|(^|[[:space:]])backup([[:space:]]|$)|(^|[[:space:]])migration([[:space:]]|$)' || true)"
if [[ -n "$busy_tasks" ]]; then
  issues+=("another build, backup, or migration process is running")
  echo "-- conflicting processes"
  printf '%s\n' "$busy_tasks"
fi

kernel_errors="$(journalctl -k -b 0 --since '-2 hours' --no-pager 2>/dev/null | grep -iE 'oom|out of memory|killed process|panic|i/o error|ext4.*error|xfs.*error' | tail -n 20 || true)"
if [[ -n "$kernel_errors" ]]; then
  issues+=("recent kernel logs contain OOM, filesystem, I/O, or panic evidence")
  echo "-- recent kernel warnings"
  printf '%s\n' "$kernel_errors"
fi

if (( ${#issues[@]} > 0 )); then
  echo "SAFETY CHECK FAILED:" >&2
  printf ' - %s\n' "${issues[@]}" >&2
  exit 42
fi

echo "SAFETY CHECK PASSED: deployment operations are allowed."
'@

$RetireLegacyWorkersScript = @'
set -euo pipefail

remote_dir="$1"
transition_mode="${2:-verify}"
resolved_dir="$(cd "$remote_dir" && pwd -P)"
if [[ "$resolved_dir" != "$remote_dir" || ! -f "$resolved_dir/docker-compose.prod.yml" || ! -f "$resolved_dir/.env.production" ]]; then
  echo "ERROR: refusing to transition workers outside the validated production directory" >&2
  exit 2
fi

cd "$resolved_dir"
compose=(docker compose --env-file .env.production -f docker-compose.prod.yml)
legacy_services=(worker-embedding worker-library worker-filter worker-vision)
queue_names=(embedding library filtering vision)

active_jobs="$("${compose[@]}" exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -t -A -c "SELECT count(*) FROM image_jobs WHERE status IN (CHR(113)||CHR(117)||CHR(101)||CHR(117)||CHR(101)||CHR(100), CHR(112)||CHR(114)||CHR(111)||CHR(99)||CHR(101)||CHR(115)||CHR(115)||CHR(105)||CHR(110)||CHR(103), CHR(99)||CHR(97)||CHR(110)||CHR(99)||CHR(101)||CHR(108)||CHR(108)||CHR(105)||CHR(110)||CHR(103));"' < /dev/null | tr -d '[:space:]')"
if [[ ! "$active_jobs" =~ ^[0-9]+$ ]]; then
  echo "ERROR: unable to verify active production jobs" >&2
  exit 2
fi
if (( active_jobs != 0 )); then
  echo "ERROR: refusing to stop legacy workers while $active_jobs image job(s) are active" >&2
  exit 42
fi

for queue in "${queue_names[@]}"; do
  length="$("${compose[@]}" exec -T redis redis-cli LLEN "$queue" < /dev/null | tr -d '[:space:]')"
  if [[ ! "$length" =~ ^[0-9]+$ ]]; then
    echo "ERROR: unable to verify Celery queue $queue" >&2
    exit 2
  fi
  if (( length != 0 )); then
    echo "ERROR: refusing to stop legacy workers while queue $queue contains $length task(s)" >&2
    exit 42
  fi
done

defined_services="$("${compose[@]}" config --services)"
services_to_stop=()
for service in "${legacy_services[@]}"; do
  if grep -Fxq "$service" <<<"$defined_services"; then
    services_to_stop+=("$service")
  fi
done
if (( ${#services_to_stop[@]} == 0 )); then
  echo "Legacy workers are already absent; no topology transition is required."
  exit 0
fi

if [[ "$transition_mode" != "execute" ]]; then
  echo "Legacy worker retirement verification passed: ${services_to_stop[*]}"
  exit 0
fi

echo "Stopping verified-idle legacy workers: ${services_to_stop[*]}"
"${compose[@]}" stop --timeout 30 "${services_to_stop[@]}"
echo "Legacy workers stopped. The full deployment safety gate must pass before release."
'@

$RestoreLegacyWorkersScript = @'
set -euo pipefail

remote_dir="$1"
resolved_dir="$(cd "$remote_dir" && pwd -P)"
cd "$resolved_dir"
legacy_containers=(
  image-intelligence-worker-embedding-1
  image-intelligence-worker-library-1
  image-intelligence-worker-filter-1
  image-intelligence-worker-vision-1
)
containers_to_start=()
for container in "${legacy_containers[@]}"; do
  if docker container inspect "$container" >/dev/null 2>&1; then
    containers_to_start+=("$container")
  fi
done
if (( ${#containers_to_start[@]} > 0 )); then
  docker start "${containers_to_start[@]}" >/dev/null
  echo "Legacy workers restored: ${containers_to_start[*]}"
fi
'@

$DeployScript = @'
set -euo pipefail

remote_dir="$1"
api_image="$2"
web_image="$3"
web_port="$4"
deployment_id="$5"
compose_payload="$6"
compose_sha256="$7"

resolved_dir="$(cd "$remote_dir" && pwd -P)"
if [[ "$resolved_dir" != "$remote_dir" || ! -f "$resolved_dir/docker-compose.prod.yml" || ! -f "$resolved_dir/.env.production" ]]; then
  echo "ERROR: refusing to deploy outside the validated production directory" >&2
  exit 2
fi

cd "$resolved_dir"
compose=(docker compose --env-file .env.production -f docker-compose.prod.yml)
deployments_dir="$resolved_dir/.deployments"
mkdir -p "$deployments_dir"
chmod 700 "$deployments_dir"
rollback_file="$deployments_dir/${deployment_id}.env.production"
rollback_compose="$deployments_dir/${deployment_id}.docker-compose.prod.yml"
cp --preserve=mode,ownership .env.production "$rollback_file"
cp --preserve=mode,ownership docker-compose.prod.yml "$rollback_compose"
chmod 600 "$rollback_file"
chmod 600 "$rollback_compose"
release_committed=0

restore_config_files() {
  cp --preserve=mode,ownership "$rollback_file" .env.production
  cp --preserve=mode,ownership "$rollback_compose" docker-compose.prod.yml
}

restore_config_on_error() {
  local exit_code="$?"
  if (( exit_code != 0 && release_committed == 0 )); then
    restore_config_files
  fi
  trap - EXIT
  exit "$exit_code"
}
trap restore_config_on_error EXIT

if [[ "$compose_payload" != "-" ]]; then
  compose_temporary="$(mktemp "$resolved_dir/docker-compose.prod.yml.XXXXXX")"
  printf '%s' "$compose_payload" | base64 -d > "$compose_temporary"
  actual_compose_sha256="$(sha256sum "$compose_temporary" | awk '{print $1}')"
  if [[ "$actual_compose_sha256" != "$compose_sha256" ]]; then
    rm -f "$compose_temporary"
    echo "ERROR: uploaded production compose checksum mismatch" >&2
    exit 2
  fi
  chmod --reference=docker-compose.prod.yml "$compose_temporary"
  chown --reference=docker-compose.prod.yml "$compose_temporary"
  if ! docker compose --env-file .env.production -f "$compose_temporary" config --quiet; then
    rm -f "$compose_temporary"
    echo "ERROR: uploaded production compose is invalid" >&2
    exit 2
  fi
  mv "$compose_temporary" docker-compose.prod.yml
fi

set_env_value() {
  local key="$1"
  local value="$2"
  local source="$resolved_dir/.env.production"
  local temporary
  temporary="$(mktemp "$resolved_dir/.env.production.XXXXXX")"
  awk -v key="$key" -v value="$value" '
    BEGIN { replaced = 0 }
    index($0, key "=") == 1 { print key "=" value; replaced = 1; next }
    { print }
    END { if (!replaced) print key "=" value }
  ' "$source" > "$temporary"
  chmod --reference="$source" "$temporary"
  chown --reference="$source" "$temporary"
  mv "$temporary" "$source"
}

restore_previous_release() {
  echo "Restoring the previous image references..." >&2
  restore_config_files
  "${compose[@]}" pull
  "${compose[@]}" up -d --remove-orphans
}

if [[ "$api_image" != "-" ]]; then
  set_env_value API_IMAGE "$api_image"
  set_env_value API_GATEWAY_IMAGE "$api_image"
fi
if [[ "$web_image" != "-" ]]; then set_env_value WEB_IMAGE "$web_image"; fi

echo "Rollback snapshot: $rollback_file"
echo "Compose rollback snapshot: $rollback_compose"
echo "Pulling immutable images..."
if ! "${compose[@]}" pull; then
  restore_config_files
  echo "Image pull failed; production configuration was restored." >&2
  exit 1
fi

echo "Starting the release..."
if ! "${compose[@]}" up -d --remove-orphans; then
  restore_previous_release
  echo "Release startup failed and was rolled back." >&2
  exit 1
fi

healthy=0
for _attempt in $(seq 1 18); do
  web_ok=0
  api_ok=0
  exited_services="$("${compose[@]}" ps --status exited --services | grep -vE '^(minio-init|migrate)$' || true)"
  restarting_services="$("${compose[@]}" ps --status restarting --services 2>/dev/null || true)"
  if curl -fsS --max-time 5 "http://127.0.0.1:${web_port}/" >/dev/null; then web_ok=1; fi
  if "${compose[@]}" exec -T api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=5)" </dev/null >/dev/null 2>&1; then api_ok=1; fi
  if (( web_ok == 1 && api_ok == 1 )) && [[ -z "$exited_services" && -z "$restarting_services" ]]; then
    healthy=1
    break
  fi
  sleep 5
done

if (( healthy != 1 )); then
  echo "Health verification failed. Limited release logs follow:" >&2
  "${compose[@]}" ps >&2 || true
  "${compose[@]}" logs --since 3m --tail 80 api worker web >&2 || true
  restore_previous_release
  sleep 5
  rollback_web_ok=0
  rollback_api_ok=0
  if curl -fsS --max-time 5 "http://127.0.0.1:${web_port}/" >/dev/null; then rollback_web_ok=1; fi
  if "${compose[@]}" exec -T api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=5)" </dev/null >/dev/null 2>&1; then rollback_api_ok=1; fi
  if (( rollback_web_ok != 1 || rollback_api_ok != 1 )); then
    echo "Rollback completed, but the previous release is not healthy." >&2
    exit 3
  fi
  echo "Health verification failed; the previous release was restored." >&2
  exit 2
fi

echo "-- deployed compose status"
"${compose[@]}" ps
echo "WEB HEALTH: HTTP 200"
echo "API HEALTH: ready"
release_committed=1
trap - EXIT
echo "Manual rollback command:"
printf 'cd %q && cp --preserve=mode,ownership %q .env.production && cp --preserve=mode,ownership %q docker-compose.prod.yml && docker compose --env-file .env.production -f docker-compose.prod.yml pull && docker compose --env-file .env.production -f docker-compose.prod.yml up -d --remove-orphans\n' "$resolved_dir" "$rollback_file" "$rollback_compose"
'@

Assert-Command git
Assert-Command docker

$candidateRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$repoRoot = (& git -C $candidateRoot rev-parse --show-toplevel).Trim()
Assert-ExitCode "Locate repository"

Push-Location $repoRoot
try {
    $dirty = & git status --porcelain --untracked-files=normal
    Assert-ExitCode "Inspect Git working tree"
    if ($dirty -and $RequireCleanGit) {
        throw "The working tree is not clean. Commit or stash every change before producing a Git-SHA production image."
    }

    $gitRevision = (& git rev-parse HEAD).Trim()
    Assert-ExitCode "Resolve Git version"
    if ($gitRevision -notmatch '^[0-9a-f]{40}$') {
        throw "Git did not return a full commit SHA."
    }

    $isCleanRelease = -not [bool]$dirty
    if ($isCleanRelease) {
        $version = $gitRevision
    }
    else {
        $timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddHHmmss")
        $uniqueSuffix = [Guid]::NewGuid().ToString("N").Substring(0, 8)
        $version = "local-$timestamp-$($gitRevision.Substring(0, 12))-$uniqueSuffix"
        Write-Warning "Deploying the current uncommitted working tree as a unique image version: $version"
        Write-Host "Changed local paths included in this release:" -ForegroundColor Yellow
        $dirty | ForEach-Object { Write-Host "  $_" }
    }

    $apiRepository = "$ImageNamespace/tuxiangshibie-api"
    $webRepository = "$ImageNamespace/tuxiangshibie-web"
    $apiImage = "${apiRepository}:$version"
    $webImage = "${webRepository}:$version"
    $deployApi = $Component -in @("All", "Api")
    $deployWeb = $Component -in @("All", "Web")
    if ($RetireLegacyWorkers -and -not $deployApi) {
        throw "RetireLegacyWorkers can only be used when the API component is deployed."
    }
    if ($PrebuiltApiImage -and -not $deployApi) {
        throw "PrebuiltApiImage can only be used when the API component is deployed."
    }
    if ($PrebuiltApiImage) {
        $expectedDigestPrefix = [Regex]::Escape("$apiRepository@sha256:")
        if ($PrebuiltApiImage -notmatch "^${expectedDigestPrefix}[0-9a-f]{64}$") {
            throw "PrebuiltApiImage must be an immutable lowercase SHA-256 digest from ${apiRepository}."
        }
    }

    if (-not $BuildOnly) {
        Assert-Command ssh
        if (-not $IdentityFile) {
            if ($env:IMAGE_INTELLIGENCE_SSH_KEY) {
                $IdentityFile = $env:IMAGE_INTELLIGENCE_SSH_KEY
            }
            elseif (-not $env:USERPROFILE) {
                throw "IdentityFile was not supplied and USERPROFILE is unavailable."
            }
            else {
                $defaultIdentity = Join-Path $env:USERPROFILE ".ssh\id_ed25519_47_111_188_85_codex"
                if (Test-Path -LiteralPath $defaultIdentity -PathType Leaf) {
                    $IdentityFile = $defaultIdentity
                }
                else {
                    $IdentityFile = Read-Host "Enter the full path of your production SSH private key"
                }
            }
        }
        $resolvedIdentity = [IO.Path]::GetFullPath($IdentityFile)
        if (-not (Test-Path -LiteralPath $resolvedIdentity -PathType Leaf)) {
            throw "SSH identity file does not exist: $resolvedIdentity"
        }
        $RemoteTarget = "$RemoteUser@$Server"
        $SshArguments = @(
            "-p", "$SshPort",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=30",
            "-o", "StrictHostKeyChecking=yes",
            "-i", $resolvedIdentity
        )
        # Every remote mutation, including stopping the legacy topology, is
        # gated by the full read-only production safety check.
        Invoke-ServerPrecheck
    }

    & docker info *> $null
    Assert-ExitCode "Connect to the local Docker engine"

    if ($deployApi) {
        if ($PrebuiltApiImage) {
            $apiPinnedImage = $PrebuiltApiImage
            Write-Host "Using prebuilt, already-verified API image: $apiPinnedImage"
        }
        else {
            if ($isCleanRelease -and (Test-RemoteImageExists $apiImage)) {
                Write-Host "Reusing existing immutable API image: $apiImage"
            }
            else {
                Write-Host "`n== Build API image $apiImage ==" -ForegroundColor Cyan
                & docker build --pull --tag $apiImage .
                Assert-ExitCode "Build API image"
                if (-not $SkipTests) {
                    Write-Host "`n== Backend tests ==" -ForegroundColor Cyan
                    $testsPath = Join-Path $repoRoot "tests"
                    $frontendPath = Join-Path $repoRoot "frontend"
                    $scriptsPath = Join-Path $repoRoot "scripts"
                    $productionComposePath = Join-Path $repoRoot "docker-compose.prod.yml"
                    $dockerfilePath = Join-Path $repoRoot "Dockerfile"
                    & docker run --rm `
                        --volume "${testsPath}:/app/tests:ro" `
                        --volume "${frontendPath}:/app/frontend:ro" `
                        --volume "${scriptsPath}:/app/scripts:ro" `
                        --volume "${productionComposePath}:/app/docker-compose.prod.yml:ro" `
                        --volume "${dockerfilePath}:/app/Dockerfile:ro" `
                        $apiImage sh -c "pip install --no-cache-dir pytest==8.4.2 pytest-asyncio==1.2.0 httpx==0.28.1 && python -m pytest -q"
                    Assert-ExitCode "Backend tests"
                }
                & docker push $apiImage
                Assert-ExitCode "Push API image"
            }
            $apiPinnedImage = Get-PinnedImageReference -Image $apiImage -Repository $apiRepository
            Write-Host "Pinned API image: $apiPinnedImage"
        }
    }

    if ($deployWeb) {
        if ($isCleanRelease -and (Test-RemoteImageExists $webImage)) {
            Write-Host "Reusing existing immutable web image: $webImage"
        }
        else {
            if (-not $SkipTests) {
                Assert-Command npm
                Write-Host "`n== Frontend tests and production build ==" -ForegroundColor Cyan
                & npm --prefix frontend ci
                Assert-ExitCode "Install frontend dependencies"
                & npm --prefix frontend test
                Assert-ExitCode "Frontend tests"
                & npm --prefix frontend run build
                Assert-ExitCode "Frontend production build"
            }
            Write-Host "`n== Build web image $webImage ==" -ForegroundColor Cyan
            & docker build --pull --tag $webImage frontend
            Assert-ExitCode "Build web image"
            & docker push $webImage
            Assert-ExitCode "Push web image"
        }
        $webPinnedImage = Get-PinnedImageReference -Image $webImage -Repository $webRepository
        Write-Host "Pinned web image: $webPinnedImage"
    }

    if ($BuildOnly) {
        Write-Host "`nImages are published. Server deployment was skipped by -BuildOnly." -ForegroundColor Green
        exit 0
    }

    $composePayload = "-"
    $composeSha256 = "-"
    if ($deployApi) {
        # Validate and serialize the topology before stopping any production
        # worker. Local .env.production intentionally does not carry release
        # image references, so use temporary non-secret validation values.
        $composePath = Join-Path $repoRoot "docker-compose.prod.yml"
        $savedImageEnvironment = @{
            API_IMAGE = $env:API_IMAGE
            API_GATEWAY_IMAGE = $env:API_GATEWAY_IMAGE
            WEB_IMAGE = $env:WEB_IMAGE
        }
        try {
            $env:API_IMAGE = $apiPinnedImage
            $env:API_GATEWAY_IMAGE = $apiPinnedImage
            $env:WEB_IMAGE = if ($deployWeb) {
                $webPinnedImage
            }
            else {
                "example.invalid/preserved-web@sha256:" + ("0" * 64)
            }
            & docker compose --env-file .env.production -f $composePath config --quiet
            Assert-ExitCode "Validate production compose"
        }
        finally {
            foreach ($name in $savedImageEnvironment.Keys) {
                $value = $savedImageEnvironment[$name]
                if ($null -eq $value) {
                    Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
                }
                else {
                    Set-Item -LiteralPath "Env:$name" -Value $value
                }
            }
        }
        $composePayload = [Convert]::ToBase64String([IO.File]::ReadAllBytes($composePath))
        $composeSha256 = (Get-FileHash -LiteralPath $composePath -Algorithm SHA256).Hash.ToLowerInvariant()
    }

    if ($RetireLegacyWorkers) {
        Write-Host "`n== Retire verified-idle legacy workers ==" -ForegroundColor Cyan
        Invoke-RemoteBash -Script $RetireLegacyWorkersScript -Arguments @(
            $RemoteDirectory,
            "execute"
        )
        try {
            Invoke-ServerPrecheck
        }
        catch {
            Restore-LegacyWorkers
            throw
        }
    }
    else {
        Invoke-ServerPrecheck
    }
    $deploymentId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ") + "-$([Guid]::NewGuid().ToString('N').Substring(0, 8))"
    $apiArgument = if ($deployApi) { $apiPinnedImage } else { "-" }
    $webArgument = if ($deployWeb) { $webPinnedImage } else { "-" }

    Write-Host "`n== Deploy $version ==" -ForegroundColor Cyan
    try {
        Invoke-RemoteBash -Script $DeployScript -Arguments @(
            $RemoteDirectory,
            $apiArgument,
            $webArgument,
            "$WebPort",
            $deploymentId,
            $composePayload,
            $composeSha256
        )
    }
    catch {
        Restore-LegacyWorkers
        throw
    }

    Write-Host "`nDeployment completed successfully: $version" -ForegroundColor Green
}
finally {
    Pop-Location
}
