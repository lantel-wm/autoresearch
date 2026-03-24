#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/run_codex_autoresearch_docker.sh [docker options] [-- launcher options]

Build or run a containerized Codex worker for autoresearch. The container only
mounts this repository into /workspace by default, so full-access Codex runs are
isolated to the mounted experiment repo rather than your whole Mac.

Docker options:
  --build            Build the image before running it.
  --build-only       Build the image and exit.
  --image IMAGE      Docker image tag. Default: autoresearch-codex:latest.
  --auth-dir DIR     Host Codex auth/config dir to mount. Default: $HOME/.codex.
  --gitconfig FILE   Host gitconfig to mount read-only. Default: $HOME/.gitconfig.
  --name NAME        Optional container name.
  -h, --help         Show this help message.

Launcher options:
  Everything after `--` is forwarded to scripts/run_codex_autoresearch.sh.
  If you pass no launcher options, the defaults are:
    --web-search live --sandbox-mode danger-full-access --approval-policy never

Examples:
  scripts/run_codex_autoresearch_docker.sh --build-only
  scripts/run_codex_autoresearch_docker.sh -- --model gpt-5.4 --iterations 5
  scripts/run_codex_autoresearch_docker.sh -- --approval-policy on-request
EOF
}

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image="autoresearch-codex:latest"
auth_dir="${CODEX_AUTH_DIR:-$HOME/.codex}"
gitconfig_path="${GITCONFIG_PATH:-$HOME/.gitconfig}"
container_name=""
build_image=0
build_only=0
launcher_args=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --build)
      build_image=1
      shift
      ;;
    --build-only)
      build_image=1
      build_only=1
      shift
      ;;
    --image)
      image="$2"
      shift 2
      ;;
    --auth-dir)
      auth_dir="$2"
      shift 2
      ;;
    --gitconfig)
      gitconfig_path="$2"
      shift 2
      ;;
    --name)
      container_name="$2"
      shift 2
      ;;
    --)
      shift
      launcher_args=("$@")
      break
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown option: %s\n' "$1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  printf 'docker command not found in PATH.\n' >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  current_context="$(docker context show 2>/dev/null || printf 'unknown')"
  current_endpoint="$(docker context inspect "$current_context" --format '{{ (index .Endpoints "docker").Host }}' 2>/dev/null || printf 'unknown')"
  printf 'Docker daemon is not reachable.\n' >&2
  printf 'Current context: %s\n' "$current_context" >&2
  printf 'Current endpoint: %s\n' "$current_endpoint" >&2
  printf 'Start Docker Desktop or another Docker daemon, then retry.\n' >&2
  printf 'Quick checks:\n' >&2
  printf '  docker info\n' >&2
  printf '  docker context ls\n' >&2
  exit 1
fi

if [[ "$build_image" -eq 1 ]]; then
  docker build \
    -f "$repo_root/docker/codex-autoresearch.Dockerfile" \
    -t "$image" \
    "$repo_root/docker"
fi

if [[ "$build_only" -eq 1 ]]; then
  exit 0
fi

mkdir -p "$repo_root/tmp/mplconfig"

if [[ ${#launcher_args[@]} -eq 0 ]]; then
  launcher_args=(
    --web-search live
    --sandbox-mode danger-full-access
    --approval-policy never
  )
fi

docker_args=(
  run
  --rm
  -it
  -v "$repo_root:/workspace"
  -w /workspace
  -e CODEX_HOME=/home/codex/.codex
  -e MPLCONFIGDIR=/workspace/tmp/mplconfig
)

if [[ -n "$container_name" ]]; then
  docker_args+=(--name "$container_name")
fi

if [[ -d "$auth_dir" ]]; then
  docker_args+=(-v "$auth_dir:/home/codex/.codex")
else
  printf 'Warning: auth dir not found: %s\n' "$auth_dir" >&2
  printf 'Run codex login on the host and mount its file-based CODEX_HOME if the container needs OpenAI auth.\n' >&2
fi

if [[ -f "$gitconfig_path" ]]; then
  docker_args+=(-v "$gitconfig_path:/home/codex/.gitconfig:ro")
fi

docker "${docker_args[@]}" "$image" ./scripts/run_codex_autoresearch.sh "${launcher_args[@]}"
