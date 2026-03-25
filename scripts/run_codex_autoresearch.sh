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
default_pause_file="$repo_root/.codex/pause_supervisor"
iterations=0
sleep_seconds=2
model=""
web_search="cached"
sandbox_mode="workspace-write"
approval_policy="on-request"
allow_shell_network=0
start_mode="resume-or-fresh"
pause_file="$default_pause_file"
dangerous=0
extra_prompt=""
output_dir="$repo_root/tmp/codex_supervisor"
state_helper="$repo_root/scripts/codex_supervisor_state.py"

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

pause_requested() {
  [[ -e "${pause_file:-$default_pause_file}" ]]
}

json_field() {
  local payload="$1"
  local field="$2"
  python3 -c 'import json,sys; data=json.loads(sys.argv[1]); value=data.get(sys.argv[2]); print("" if value is None else value)' "$payload" "$field"
}

run_preflight() {
  local payload
  payload="$(python3 "$state_helper" preflight --repo-root "$repo_root")"
  local ok
  ok="$(json_field "$payload" "ok")"
  if [[ "$ok" != "True" && "$ok" != "true" ]]; then
    local reason details
    reason="$(json_field "$payload" "reason")"
    details="$(json_field "$payload" "details")"
    printf 'Supervisor preflight failed: %s\n' "$reason" >&2
    if [[ -n "$details" ]]; then
      printf 'Details: %s\n' "$details" >&2
    fi
    exit 1
  fi
  if [[ "$(json_field "$payload" "restored_train")" == "True" || "$(json_field "$payload" "restored_train")" == "true" ]]; then
    printf '[%s] restored train.py to latest kept baseline\n' "$(date '+%Y-%m-%d %H:%M:%S')"
  fi
}

required_category_payload() {
  python3 "$state_helper" required-category --repo-root "$repo_root"
}
build_prompt() {
  local required_category="$1"
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
  printf '\nThis step MUST be a %s experiment.\n' "$required_category"
  case "$required_category" in
    factor)
      printf 'Do not run a label, model, or strategy experiment in this step.\n'
      ;;
    label)
      printf 'Do not run a factor, model, or strategy experiment in this step.\n'
      ;;
  esac
}

common_args_fresh=(
  --cd "$repo_root"
  --output-last-message "$output_dir/last_message.txt"
  --disable codex_hooks
  -c "web_search=\"$web_search\""
  --sandbox "$sandbox_mode"
  -c "approval_policy=\"$approval_policy\""
)
common_args_resume=(
  --output-last-message "$output_dir/last_message.txt"
  --disable codex_hooks
  -c "web_search=\"$web_search\""
  -c "approval_policy=\"$approval_policy\""
)
if [[ -n "$model" ]]; then
  common_args_fresh+=(--model "$model")
  common_args_resume+=(--model "$model")
fi
if [[ "$allow_shell_network" -eq 1 ]]; then
  common_args_fresh+=(-c 'sandbox_workspace_write.network_access=true')
  common_args_resume+=(-c 'sandbox_workspace_write.network_access=true')
fi
if [[ "$dangerous" -eq 1 ]]; then
  common_args_fresh+=(--dangerously-bypass-approvals-and-sandbox)
  common_args_resume+=(--dangerously-bypass-approvals-and-sandbox)
fi

run_step() {
  local step="$1"
  local required_category="$2"
  local prompt
  prompt="$(build_prompt "$required_category")"
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
    local resume_rc
    if "$codex_bin" exec resume --last "${common_args_resume[@]}" "$prompt"; then
      return 0
    else
      resume_rc=$?
    fi
    if [[ "$start_mode" == "resume-only" ]]; then
      return "$resume_rc"
    fi
    printf 'Resume failed for step %s (exit %s). Starting a fresh Codex session.\n' "$step" "$resume_rc" >&2
  fi

  "$codex_bin" exec "${common_args_fresh[@]}" "$prompt"
}

record_step_result() {
  local required_category="$1"
  local payload
  payload="$(python3 "$state_helper" record-result --repo-root "$repo_root" --required-category "$required_category")"
  local valid commit status category
  valid="$(json_field "$payload" "valid")"
  commit="$(json_field "$payload" "commit")"
  status="$(json_field "$payload" "status")"
  category="$(json_field "$payload" "category")"
  if [[ "$valid" != "True" && "$valid" != "true" ]]; then
    printf '[%s] invalid experiment category: expected %s, got %s (%s)\n' \
      "$(date '+%Y-%m-%d %H:%M:%S')" "$required_category" "$category" "$commit" >&2
    if [[ "$status" == "keep" ]]; then
      git revert --no-edit "$commit"
      printf '[%s] reverted invalid keep candidate %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$commit" >&2
    fi
  fi
}

step=1
while :; do
  run_preflight

  if pause_requested; then
    current_pause_file="${pause_file:-$default_pause_file}"
    printf '[%s] pause requested via %s; stopping before step %s\n' \
      "$(date '+%Y-%m-%d %H:%M:%S')" "$current_pause_file" "$step"
    exit 0
  fi
  if [[ "$iterations" -gt 0 && "$step" -gt "$iterations" ]]; then
    break
  fi

  required_payload="$(required_category_payload)"
  required_category="$(json_field "$required_payload" "required_category")"
  printf '\n[%s] starting Codex step %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$step"
  printf '[%s] required experiment category: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$required_category"
  if run_step "$step" "$required_category"; then
    printf '[%s] step %s completed\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$step"
  else
    rc=$?
    printf '[%s] step %s failed with exit code %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$step" "$rc" >&2
    exit "$rc"
  fi

  record_step_result "$required_category"

  if [[ -f "$output_dir/last_message.txt" ]]; then
    cp "$output_dir/last_message.txt" "$output_dir/step_$(printf '%04d' "$step").txt"
  fi

  step=$((step + 1))
  if pause_requested; then
    current_pause_file="${pause_file:-$default_pause_file}"
    printf '[%s] pause requested via %s; current step finished, stopping cleanly\n' \
      "$(date '+%Y-%m-%d %H:%M:%S')" "$current_pause_file"
    exit 0
  fi
  if [[ "$iterations" -eq 0 || "$step" -le "$iterations" ]]; then
    sleep "$sleep_seconds"
  fi
done
