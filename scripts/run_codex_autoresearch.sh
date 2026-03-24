#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/run_codex_autoresearch.sh [options]

Run Codex under an external supervisor loop so each invocation completes
exactly one autoresearch iteration and the shell loop provides persistence.

Options:
  --iterations N     Number of Codex invocations to run. Default: 0 (infinite).
  --sleep SECONDS    Delay between invocations. Default: 2.
  --model MODEL      Optional Codex model override.
  --web-search MODE  One of: cached, live, disabled. Default: cached.
  --sandbox-mode M   One of: read-only, workspace-write, danger-full-access.
                     Default: workspace-write.
  --approval-policy P
                     One of: on-request, never, untrusted. Default: on-request.
  --allow-shell-network
                     Allow network access for shell commands in workspace-write mode.
  --fresh            Start with a fresh Codex session instead of resuming the latest one.
  --resume-only      Only resume the latest session. Fail if no resumable session exists.
  --dangerous        Pass --dangerously-bypass-approvals-and-sandbox to Codex.
  --extra-prompt TXT Append one extra instruction to every Codex invocation.
  -h, --help         Show this help message.

Environment:
  CODEX_BIN          Absolute path to the Codex CLI binary. Overrides PATH lookup.
EOF
}

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
iterations=0
sleep_seconds=2
model=""
web_search="cached"
sandbox_mode="workspace-write"
approval_policy="on-request"
allow_shell_network=0
start_mode="resume-or-fresh"
dangerous=0
extra_prompt=""
output_dir="$repo_root/tmp/codex_supervisor"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --iterations)
      iterations="$2"
      shift 2
      ;;
    --sleep)
      sleep_seconds="$2"
      shift 2
      ;;
    --model)
      model="$2"
      shift 2
      ;;
    --web-search)
      web_search="$2"
      shift 2
      ;;
    --sandbox-mode)
      sandbox_mode="$2"
      shift 2
      ;;
    --approval-policy)
      approval_policy="$2"
      shift 2
      ;;
    --allow-shell-network)
      allow_shell_network=1
      shift
      ;;
    --fresh)
      start_mode="fresh"
      shift
      ;;
    --resume-only)
      start_mode="resume-only"
      shift
      ;;
    --dangerous)
      dangerous=1
      shift
      ;;
    --extra-prompt)
      extra_prompt="$2"
      shift 2
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

case "$web_search" in
  cached|live|disabled)
    ;;
  *)
    printf 'Invalid --web-search mode: %s\n' "$web_search" >&2
    usage >&2
    exit 1
    ;;
esac

case "$sandbox_mode" in
  read-only|workspace-write|danger-full-access)
    ;;
  *)
    printf 'Invalid --sandbox-mode: %s\n' "$sandbox_mode" >&2
    usage >&2
    exit 1
    ;;
esac

case "$approval_policy" in
  on-request|never|untrusted)
    ;;
  *)
    printf 'Invalid --approval-policy: %s\n' "$approval_policy" >&2
    usage >&2
    exit 1
    ;;
esac

codex_bin="${CODEX_BIN:-}"
if [[ -n "$codex_bin" ]]; then
  if [[ ! -x "$codex_bin" ]]; then
    printf 'CODEX_BIN is set but not executable: %s\n' "$codex_bin" >&2
    exit 1
  fi
elif codex_bin="$(command -v codex 2>/dev/null)"; then
  :
elif [[ -x "/Applications/Codex.app/Contents/Resources/codex" ]]; then
  codex_bin="/Applications/Codex.app/Contents/Resources/codex"
else
  printf 'Could not find Codex CLI.\n' >&2
  printf 'Set CODEX_BIN=/absolute/path/to/codex or add codex to PATH.\n' >&2
  exit 1
fi

mkdir -p "$output_dir"

build_prompt() {
  cat <<'EOF'
You are operating this repository under an external supervisor.
Read README.md and program.md as needed, but treat them as durable policy and state,
not as a reason to keep this single session alive forever.

Complete exactly one full autoresearch iteration:
1. Inspect git state, results.tsv, run.json, run.log, train.py, and the current kept baseline.
2. If the web research policy applies and web search is available in this Codex mode, do a short research pass.
3. If web search is unavailable in this mode, note that limitation briefly and continue with the best local hypothesis.
4. Modify only train.py for exactly one hypothesis that follows the repo policy.
5. Commit the change.
6. Run:
   MPLCONFIGDIR=$PWD/tmp/mplconfig \
   QLIB_PROVIDER_URI=${QLIB_PROVIDER_URI:-$PWD/data/qlib_bin_daily_hfq} \
   conda run -n qlib python train.py > run.log 2>&1
7. Read run.json or run.log.
8. If status is keep, keep the commit.
9. If status is discard, revert to the previous kept commit.
10. If status is crash, fix once if the issue is trivial and the idea still makes sense; otherwise move on.
11. Leave the repository in a clean state that is ready for the next supervised iteration, then stop.

Do not ask whether to continue. The supervisor will launch the next step.
Do not stop before either finishing one completed iteration or reporting a concrete blocker.
EOF
}

common_args=(
  --cd "$repo_root"
  --output-last-message "$output_dir/last_message.txt"
  --disable codex_hooks
  -c "web_search=\"$web_search\""
  --sandbox "$sandbox_mode"
  -c "approval_policy=\"$approval_policy\""
)
if [[ -n "$model" ]]; then
  common_args+=(--model "$model")
fi
if [[ "$allow_shell_network" -eq 1 ]]; then
  common_args+=(-c 'sandbox_workspace_write.network_access=true')
fi
if [[ "$dangerous" -eq 1 ]]; then
  common_args+=(--dangerously-bypass-approvals-and-sandbox)
fi

run_step() {
  local step="$1"
  local prompt
  prompt="$(build_prompt)"
  if [[ -n "$extra_prompt" ]]; then
    prompt+=$'\n\nAdditional instruction:\n'"$extra_prompt"
  fi

  local try_resume=0
  if [[ "$step" -gt 1 ]]; then
    try_resume=1
  elif [[ "$start_mode" != "fresh" ]]; then
    try_resume=1
  fi

  if [[ "$try_resume" -eq 1 ]]; then
    if "$codex_bin" exec resume --last "${common_args[@]}" "$prompt"; then
      return 0
    fi
    local resume_rc=$?
    if [[ "$start_mode" == "resume-only" ]]; then
      return "$resume_rc"
    fi
    printf 'Resume failed for step %s (exit %s). Starting a fresh Codex session.\n' "$step" "$resume_rc" >&2
  fi

  "$codex_bin" exec "${common_args[@]}" "$prompt"
}

step=1
while :; do
  if [[ "$iterations" -gt 0 && "$step" -gt "$iterations" ]]; then
    break
  fi

  printf '\n[%s] starting Codex step %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$step"
  if run_step "$step"; then
    printf '[%s] step %s completed\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$step"
  else
    rc=$?
    printf '[%s] step %s failed with exit code %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$step" "$rc" >&2
    exit "$rc"
  fi

  if [[ -f "$output_dir/last_message.txt" ]]; then
    cp "$output_dir/last_message.txt" "$output_dir/step_$(printf '%04d' "$step").txt"
  fi

  step=$((step + 1))
  if [[ "$iterations" -eq 0 || "$step" -le "$iterations" ]]; then
    sleep "$sleep_seconds"
  fi
done
