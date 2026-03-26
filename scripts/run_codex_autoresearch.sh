#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/run_codex_autoresearch.sh [options]

Run Codex under an external supervisor loop so each invocation completes
exactly one autoresearch iteration and the shell loop provides persistence.
This is the reliable Codex workaround for interactive never-stop limitations.

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
  --pause-file PATH  Sentinel file that pauses the supervisor before the next step.
                     Default: .codex/pause_supervisor
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
current_branch=""
current_keep_commit=""
current_signature=""
loaded_step_branch=""
step=0
local_steps_run=0

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
    --pause-file)
      pause_file="$2"
      shift 2
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
  current_branch="$(git -C "$repo_root" branch --show-current)"
  current_keep_commit="$(json_field "$payload" "latest_keep_commit")"
  current_signature="${current_branch}"
  if [[ "$(json_field "$payload" "restored_train")" == "True" || "$(json_field "$payload" "restored_train")" == "true" ]]; then
    printf '[%s] restored train.py to latest kept baseline\n' "$(date '+%Y-%m-%d %H:%M:%S')"
  fi
}

latest_results_commit() {
  awk -F '\t' 'NR > 1 { last = $1 } END { print last }' "$repo_root/results.tsv" 2>/dev/null
}

last_message_has_blocker() {
  [[ -f "$output_dir/last_message.txt" ]] && grep -Eiq 'concrete blocker|I.?m stopping here because' "$output_dir/last_message.txt"
}

branch_slug() {
  local branch="$1"
  branch="${branch//\//__}"
  branch="${branch// /_}"
  printf '%s' "$branch"
}

branch_step_file() {
  local branch="$1"
  printf '%s/step_counter_%s.txt' "$output_dir" "$(branch_slug "$branch")"
}

branch_artifact_dir() {
  local branch="$1"
  printf '%s/%s' "$output_dir" "$(branch_slug "$branch")"
}

load_branch_step() {
  local branch="$1"
  local file max_step
  file="$(branch_step_file "$branch")"
  if [[ -f "$file" ]]; then
    cat "$file"
    return 0
  fi

  max_step="$(find "$output_dir" -maxdepth 1 -type f -name 'step_*.txt' -print 2>/dev/null | \
    sed -E 's#^.*/step_([0-9]+)\.txt$#\1#' | sort -n | tail -n 1)"
  if [[ -n "$max_step" ]]; then
    printf '%s' "$((10#$max_step + 1))"
  else
    printf '1'
  fi
}

save_branch_step() {
  local branch="$1"
  local next_step="$2"
  printf '%s\n' "$next_step" > "$(branch_step_file "$branch")"
}

save_step_run_json() {
  local branch="$1"
  local step="$2"
  local target_dir target_file
  [[ -f "$repo_root/run.json" ]] || return 0
  target_dir="$(branch_artifact_dir "$branch")"
  mkdir -p "$target_dir"
  target_file="$target_dir/run_$(printf '%04d' "$step").json"
  cp "$repo_root/run.json" "$target_file"
}

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

  local signature_file stored_signature
  signature_file="$output_dir/session_signature.txt"
  stored_signature=""
  if [[ -f "$signature_file" ]]; then
    stored_signature="$(<"$signature_file")"
  fi
  if [[ -z "$stored_signature" && -f "$output_dir/last_message.txt" ]]; then
    printf 'Missing session signature for existing supervisor state; starting a fresh Codex session.\n' >&2
    try_resume=0
  elif [[ -n "$stored_signature" && "$stored_signature" != "$current_signature" ]]; then
    printf 'Session signature changed (%s -> %s); starting a fresh Codex session.\n' \
      "$stored_signature" "$current_signature" >&2
    try_resume=0
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
  local payload
  payload="$(python3 "$state_helper" record-result --repo-root "$repo_root")"
  local valid_reason commit status category
  valid_reason="$(json_field "$payload" "valid_reason")"
  commit="$(json_field "$payload" "commit")"
  status="$(json_field "$payload" "status")"
  category="$(json_field "$payload" "category")"
  printf '[%s] recorded %s experiment result: %s (%s)\n' \
    "$(date '+%Y-%m-%d %H:%M:%S')" "$category" "$status" "$commit"
  if [[ "$valid_reason" == "unknown_category" && "$status" == "keep" ]]; then
    git revert --no-edit "$commit"
    printf '[%s] reverted unsupported keep candidate %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$commit" >&2
  fi
}

while :; do
  run_preflight

  if [[ "$loaded_step_branch" != "$current_branch" ]]; then
    step="$(load_branch_step "$current_branch")"
    loaded_step_branch="$current_branch"
  fi

  if pause_requested; then
    current_pause_file="${pause_file:-$default_pause_file}"
    printf '[%s] pause requested via %s; stopping before step %s\n' \
      "$(date '+%Y-%m-%d %H:%M:%S')" "$current_pause_file" "$step"
    exit 0
  fi

  if [[ "$iterations" -gt 0 && "$local_steps_run" -ge "$iterations" ]]; then
    break
  fi

  printf '\n[%s] starting Codex step %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$step"
  previous_result_commit="$(latest_results_commit)"
  if run_step "$step"; then
    printf '[%s] step %s completed\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$step"
  else
    rc=$?
    printf '[%s] step %s failed with exit code %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$step" "$rc" >&2
    exit "$rc"
  fi

  latest_result_commit="$(latest_results_commit)"
  if [[ "$latest_result_commit" == "$previous_result_commit" ]]; then
    if last_message_has_blocker; then
      printf '[%s] concrete blocker reported without a new result row; stopping supervisor loop\n' \
        "$(date '+%Y-%m-%d %H:%M:%S')" >&2
      exit 0
    fi
    printf '[%s] no new results.tsv row was produced by step %s; stopping supervisor loop\n' \
      "$(date '+%Y-%m-%d %H:%M:%S')" "$step" >&2
    exit 1
  fi

  printf '%s\n' "$current_signature" > "$output_dir/session_signature.txt"

  record_step_result
  save_step_run_json "$current_branch" "$step"

  if [[ -f "$output_dir/last_message.txt" ]]; then
    cp "$output_dir/last_message.txt" "$output_dir/step_$(printf '%04d' "$step").txt"
  fi

  local_steps_run=$((local_steps_run + 1))
  step=$((step + 1))
  save_branch_step "$current_branch" "$step"
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
