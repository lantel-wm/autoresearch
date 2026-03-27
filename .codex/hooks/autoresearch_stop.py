#!/usr/bin/env python3

from __future__ import annotations

import json

from autoresearch_state import (
    allow_stop_requested,
    has_merge_conflicts,
    load_payload,
    provider_missing,
    repo_root,
    state_block,
)


def main() -> int:
    payload = load_payload()
    root = repo_root(payload)

    if allow_stop_requested(root):
        print(
            json.dumps(
                {
                    "systemMessage": (
                        "Autoresearch stop hook is disabled by AUTORESEARCH_ALLOW_STOP=1 "
                        "or .codex/allow_stop, so the agent may stop normally."
                    )
                }
            )
        )
        return 0

    if provider_missing(root):
        print(
            json.dumps(
                {
                    "systemMessage": (
                        "Autoresearch stop hook detected that the Qlib provider is missing "
                        "and is allowing the agent to stop so the human can fix the data path."
                    )
                }
            )
        )
        return 0

    if has_merge_conflicts(root):
        print(
            json.dumps(
                {
                    "systemMessage": (
                        "Autoresearch stop hook detected unresolved merge conflicts and is "
                        "allowing the agent to stop for human intervention."
                    )
                }
            )
        )
        return 0

    stop_hook_active = bool(payload.get("stop_hook_active"))
    system_message = (
        "Autoresearch stop hook is continuing the experiment loop."
        if not stop_hook_active
        else "Autoresearch stop hook is keeping the experiment loop alive again."
    )

    if stop_hook_active:
        reason = (
            "Do not stop. Continue the autoresearch loop from the current repository state. "
            "Use git state, run_state.json, results.tsv, run.json, and train.py to choose the next "
            "concrete experiment action."
        )
    else:
        reason = (
            "Continue the autoresearch experiment loop instead of stopping.\n"
            f"{state_block(root)}\n"
            "Next-step policy:\n"
            "- Inspect git state, run_state.json, results.tsv, run.json, and train.py.\n"
            "- If the last run finished, choose the next single hypothesis and execute it.\n"
            "- If the last run crashed, fix it once if the issue is trivial and the idea still makes sense; otherwise move on.\n"
            "- Only stop if there is a concrete blocker that truly requires human intervention."
        )

    print(json.dumps({"systemMessage": system_message, "decision": "block", "reason": reason}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
