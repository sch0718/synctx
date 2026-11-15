#!/bin/bash
# One foreground Viewer container; no Compose, gateway, CDN or Marp process.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

fail() { printf 'run-viewer: %s\n' "$1" >&2; exit 2; }
usage() {
    cat <<'HELP'
Usage: ./run.sh --docker (--repo URL_OR_PATH [--repo ...] | --config FILE) [options]

  --image IMAGE       Prebuilt viewer image with a SemVer-git-sha tag (no latest)
  --build             Build the local Viewer through scripts/local/steps/build.sh
                      Default image: ${DOCKER_REGISTRY:-synctx}/viewer:0.8.1-<git-sha>
                      (without DOCKER_REGISTRY it must already exist locally; never pulled)
  --bind ADDRESS      Host IP to publish on (default 127.0.0.1)
  --port PORT         Host port (default 18000; container uses 8000)
  --name NAME         Container name (default synctx-viewer)
  --volume NAME       Named cache volume (default synctx-viewer-cache)
  --env NAME          Forward another exported variable by name, e.g. credential_env
  --ref REF           Git branch/ref for --repo (default main; not with --config)
  --public-base-url URL  Public URL for exported source links
  --refresh-interval SECONDS  Repository refresh interval (default 60; 0 disables)
  -h, --help          Show this help

GIT_READ_TOKEN and SYNCTX_SERVICE_TOKEN are forwarded by name. Local --repo
paths and --config are resolved from the caller's directory and mounted read-only.
Local file:// URLs are supported. Linked worktrees/submodules need a remote Git URL.
Ctrl-C stops the foreground container; --rm removes it, retaining the cache volume.
HELP
}
require_value() {
    [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || fail "$1 requires a value"
}

# Decode only local file URLs, byte by byte: never interpret shell syntax or
# backslash escapes from input, and do not require host Python for --image.
file_url_path() {
    local remainder authority encoded byte character
    remainder="${1#*://}"
    [[ "$remainder" == */* ]] || fail "file URL requires an absolute local path"
    authority="${remainder%%/*}"
    case "$authority" in
        ""|[lL][oO][cC][aA][lL][hH][oO][sS][tT]) ;;
        *) fail "unsupported file URL host; use an empty host or localhost" ;;
    esac
    encoded="/${remainder#*/}"
    [[ "$encoded" != *\?* && "$encoded" != *\#* && ! "$encoded" =~ [[:cntrl:]] ]] || fail "invalid file URL path"
    LOCAL_REPO_PATH=""
    while [[ -n "$encoded" ]]; do
        character="${encoded:0:1}"
        if [[ "$character" == % ]]; then
            byte="${encoded:1:2}"
            [[ "$byte" =~ ^[a-fA-F0-9]{2}$ ]] || fail "invalid percent escape in file URL"
            [[ $((16#$byte)) -ge 32 && $((16#$byte)) -ne 127 ]] || fail "invalid control byte in file URL"
            printf -v character '%b' "\\x${byte}"
            encoded="${encoded:3}"
        else
            encoded="${encoded:1}"
        fi
        LOCAL_REPO_PATH+="$character"
    done
}

BIND=127.0.0.1
PORT=18000
NAME=synctx-viewer
VOLUME=synctx-viewer-cache
IMAGE=""
REF_GIVEN=false
BUILD=false
CONFIG=""
REPO_COUNT=0
APP_ARGS=()
REPO_MOUNTS=()
MOUNTED_REPOS=()
ENV_NAMES=(GIT_READ_TOKEN SYNCTX_SERVICE_TOKEN SYNCTX_RENDER_SECRET SYNCTX_RENDER_SECRET_PREVIOUS)
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --build) BUILD=true; shift ;;
        --image|--bind|--port|--name|--volume)
            require_value "$@"
            case "$1" in
                --image) IMAGE="$2" ;;
                --bind) BIND="$2" ;;
                --port) PORT="$2" ;;
                --name) NAME="$2" ;;
                --volume) VOLUME="$2" ;;
            esac
            shift 2 ;;
        --env)
            require_value "$@"
            [[ "$2" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]] || fail "--env expects a variable name, never NAME=value"
            case "$2" in
                SYNCTX_ENABLED_SERVICES|SYNCTX_VIEWER_PUBLIC_BASE_URL|SYNCTX_CONFIG_PATH|GIT_CONFIG_COUNT|GIT_CONFIG_KEY_*|GIT_CONFIG_VALUE_*|SYNCTX_RENDER_SECRET_FILE|SYNCTX_RENDER_SECRET_PREVIOUS_FILE|SYNCTX_RENDER_SECRET_FILE_MOUNT|SYNCTX_RENDER_SECRET_PREVIOUS_FILE_MOUNT)
                    fail "--env cannot override launcher-managed variables" ;;
            esac
            ENV_NAMES+=("$2"); shift 2 ;;
        --config)
            require_value "$@"
            [[ -z "$CONFIG" && "$REPO_COUNT" == 0 ]] || fail "use either --config or --repo"
            [[ -f "$2" ]] || fail "config file does not exist"
            CONFIG="$(cd "$(dirname "$2")" && pwd -P)/$(basename "$2")"
            [[ "$CONFIG" != *:* ]] || fail "config path cannot contain a colon (Docker bind syntax)"
            APP_ARGS+=(--config /etc/synctx/standalone.yaml)
            shift 2 ;;
        --repo)
            require_value "$@"
            [[ -z "$CONFIG" ]] || fail "use either --config or --repo"
            repo="$2"
            # Git URLs and scp-style SSH stay untouched. Local file URLs select
            # the same directory as a path, including percent-encoded spaces.
            local_source=false
            case "$repo" in
                [fF][iI][lL][eE]://*)
                    file_url_path "$repo"
                    repo="$LOCAL_REPO_PATH"
                    local_source=true ;;
                *:*) ;;
                *) local_source=true ;;
            esac
            # Mount only the explicitly supplied directory, never Git admin or
            # parent directories referenced by a linked worktree/submodule.
            if [[ "$local_source" == true ]]; then
                [[ -d "$repo" ]] || fail "local repo directory does not exist"
                repo="$(cd -- "$repo" && pwd -P)"
                [[ "$repo" != *:* ]] || fail "local repo path cannot contain a colon (Docker bind syntax)"
                [[ ! -f "$repo/.git" ]] || fail "local linked worktree or submodule is unsupported; use the remote Git URL"
                mounted=false
                for previous in ${MOUNTED_REPOS[@]+"${MOUNTED_REPOS[@]}"}; do
                    [[ "$previous" != "$repo" ]] || mounted=true
                done
                if [[ "$mounted" == false ]]; then
                    REPO_MOUNTS+=(--volume "${repo}:${repo}:ro")
                    MOUNTED_REPOS+=("$repo")
                fi
            fi
            APP_ARGS+=(--repo "$repo")
            REPO_COUNT=$((REPO_COUNT + 1))
            shift 2 ;;
        --ref|--public-base-url|--refresh-interval)
            require_value "$@"
            [[ "$1" != --ref ]] || REF_GIVEN=true
            APP_ARGS+=("$1" "$2"); shift 2 ;;
        --host) fail "use --bind for the Docker host address" ;;
        --cache-dir) fail "use --volume for the Docker cache" ;;
        *) fail "unknown option: $1" ;;
    esac
done
[[ "$REPO_COUNT" -gt 0 || -n "$CONFIG" ]] || fail "--repo or --config is required"
[[ -z "$CONFIG" || "$REF_GIVEN" == false ]] || fail "--ref applies only to --repo; set default_ref in the --config file"
[[ "$BUILD" == false || -z "$IMAGE" ]] || fail "--build and --image are mutually exclusive"
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] || fail "port must be between 1 and 65535"
PORT=$((10#$PORT))
[[ "$PORT" -ge 1 && "$PORT" -le 65535 ]] || fail "port must be between 1 and 65535"
[[ "$NAME" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || fail "invalid container name"
[[ "$VOLUME" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]+$ ]] || fail "--volume must be a named Docker volume"

RENDER_ARGS=()
for env_name in SYNCTX_RENDER_SECRET SYNCTX_RENDER_SECRET_PREVIOUS; do
    file_name="${env_name}_FILE"
    if [[ -n "${!file_name:-}" ]]; then
        [[ -z "${!env_name:-}" ]] || fail "${env_name} and ${file_name} are mutually exclusive"
        host_file="${!file_name}"
        [[ "$host_file" != *:* ]] || fail "${file_name} path cannot contain a colon"
        [[ -f "$host_file" && -r "$host_file" ]] || fail "${file_name} file does not exist or is unreadable"
        host_file="$(cd "$(dirname "$host_file")" && pwd -P)/$(basename "$host_file")"
        [[ "$host_file" != *:* ]] || fail "${file_name} path cannot contain a colon"
        target=/run/secrets/synctx-render-secret
        [[ "$env_name" != SYNCTX_RENDER_SECRET_PREVIOUS ]] || target=/run/secrets/synctx-render-secret-previous
        RENDER_ARGS+=(--volume "${host_file}:${target}:ro" --env "${file_name}=${target}")
    fi
done

# Docker publication needs a literal IP, rather than a hostname or host:port.
if [[ "$BIND" == *:* ]]; then
    [[ "$BIND" =~ ^[a-fA-F0-9:]+$ && "$BIND" != *:::* ]] || fail "invalid --bind IP address"
    [[ "$BIND" != :* || "$BIND" == ::* ]] || fail "invalid --bind IP address"
    [[ "$BIND" != *: || "$BIND" == *:: ]] || fail "invalid --bind IP address"
    remainder="$BIND"
    groups=0
    while [[ "$remainder" == *:* ]]; do
        group="${remainder%%:*}"
        [[ ${#group} -le 4 ]] || fail "invalid --bind IP address"
        [[ -z "$group" ]] || groups=$((groups + 1))
        remainder="${remainder#*:}"
    done
    [[ ${#remainder} -le 4 ]] || fail "invalid --bind IP address"
    [[ -z "$remainder" ]] || groups=$((groups + 1))
    if [[ "$BIND" == *::* ]]; then
        remainder="${BIND#*::}"
        [[ "$remainder" != *::* && "$groups" -lt 8 ]] || fail "invalid --bind IP address"
    else
        [[ "$groups" == 8 && "$BIND" != :* && "$BIND" != *: ]] || fail "invalid --bind IP address"
    fi
    PUBLICATION="[${BIND}]:${PORT}:8000"
else
    [[ "$BIND" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]] || fail "invalid --bind IP address"
    IFS=. read -r first second third fourth <<< "$BIND"
    for octet in "$first" "$second" "$third" "$fourth"; do
        [[ $((10#$octet)) -le 255 ]] || fail "invalid --bind IP address"
    done
    PUBLICATION="${BIND}:${PORT}:8000"
fi

REGISTRY="${DOCKER_REGISTRY:-synctx}"
REGISTRY="${REGISTRY%/}"
# The default synctx/ prefix names no registry we control: Docker would pull it
# from Docker Hub. Use only a local image then; a custom DOCKER_REGISTRY or an
# explicit --image keeps Docker's normal pull policy.
LOCAL_ONLY=false
if [[ -z "$IMAGE" ]]; then
    [[ -n "${DOCKER_REGISTRY:-}" ]] || LOCAL_ONLY=true
    SHA="$(git -C "$PROJECT_ROOT" rev-parse --short HEAD 2>/dev/null)" || fail "default image needs a Git checkout; use --image for a prebuilt image"
    TAG="0.8.1-${SHA}"
    IMAGE="${REGISTRY}/viewer:${TAG}"
fi
# Require an explicit SemVer-SHA tag, including for prebuilt images. Keep the
# reference a single Docker argument and reject implicit latest and digests.
IMAGE_REPOSITORY="${IMAGE%:*}"
IMAGE_TAG="${IMAGE##*:}"
[[ "$IMAGE_TAG" =~ ^[0-9]+\.[0-9]+\.[0-9]+-[0-9a-f]{7,40}$ ]] || fail "image tag must use SemVer-git-sha, e.g. 0.8.1-abcdef0 (no latest)"
[[ "$IMAGE_REPOSITORY" =~ ^[a-z0-9]+([._-][a-z0-9]+)*(:[0-9]+)?(/[a-z0-9]+([._-][a-z0-9]+)*)*$ ]] || fail "invalid image repository"
command -v docker >/dev/null 2>&1 || fail "docker is required"

if [[ "$BUILD" == true ]]; then
    DOCKER_REGISTRY="$REGISTRY" "${PROJECT_ROOT}/scripts/local/steps/build.sh" --only viewer --no-load --tag "$IMAGE_TAG"
elif [[ "$LOCAL_ONLY" == true ]]; then
    docker image inspect "$IMAGE" >/dev/null 2>&1 \
        || fail "image ${IMAGE} is not available locally; run with --build, or pass --image or DOCKER_REGISTRY"
fi

public_host="$BIND"
case "$BIND" in
    0.0.0.0) public_host=127.0.0.1 ;;
    ::) public_host=::1 ;;
esac
[[ "$public_host" != *:* ]] || public_host="[${public_host}]"
export SYNCTX_VIEWER_PUBLIC_BASE_URL="${SYNCTX_VIEWER_PUBLIC_BASE_URL:-http://${public_host}:${PORT}}"
DOCKER_ARGS=(run --rm --name "$NAME" --publish "$PUBLICATION" --volume "${VOLUME}:/data/viewer")
[[ "$LOCAL_ONLY" == false ]] || DOCKER_ARGS+=(--pull never)
[[ -z "$CONFIG" ]] || DOCKER_ARGS+=(--volume "${CONFIG}:/etc/synctx/standalone.yaml:ro")
DOCKER_ARGS+=(${REPO_MOUNTS[@]+"${REPO_MOUNTS[@]}"})
DOCKER_ARGS+=(${RENDER_ARGS[@]+"${RENDER_ARGS[@]}"})
for env_name in "${ENV_NAMES[@]}"; do
    DOCKER_ARGS+=(--env "$env_name")
done
DOCKER_ARGS+=(--env SYNCTX_VIEWER_PUBLIC_BASE_URL --env SYNCTX_ENABLED_SERVICES=viewer)
# The container runs as root, while a bind mount keeps the host owner's uid.
# Git (2.45.1+) refuses to clone a local source owned by another uid, so trust
# exactly the mounted directories via command-line scoped config.
if [[ ${#MOUNTED_REPOS[@]} -gt 0 ]]; then
    DOCKER_ARGS+=(--env "GIT_CONFIG_COUNT=${#MOUNTED_REPOS[@]}")
    index=0
    for mounted_repo in "${MOUNTED_REPOS[@]}"; do
        DOCKER_ARGS+=(--env "GIT_CONFIG_KEY_${index}=safe.directory" --env "GIT_CONFIG_VALUE_${index}=${mounted_repo}")
        index=$((index + 1))
    done
fi
exec docker "${DOCKER_ARGS[@]}" "$IMAGE" python /app/services/viewer/src/standalone.py \
    ${APP_ARGS[@]+"${APP_ARGS[@]}"} --host 0.0.0.0 --port 8000 --cache-dir /data/viewer
