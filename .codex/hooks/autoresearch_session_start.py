#!/usr/bin/env python3

from __future__ import annotations

import json

from autoresearch_state import load_payload, repo_root, state_block


def main() -> int:
    payload = load_payload()
    root = repo_root(payload)
    additional_context = (
        "Autoresearch repo context:\n"
        f"{state_block(root)}\n"
        "Operating rules:\n"
        "- Treat prepare.py as the fixed harness.\n"
        "- Only mutate train.py during experiments.\n"
        "- Use program.md as the research policy.\n"
        "- Prefer factors first, labels second, model tweaks third, strategy tweaks last.\n"
        "- Use git, results.tsv, run.json, and run.log as the durable state of the run."
    )
    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": additional_context,
        }
    }
    print(json.dumps(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
