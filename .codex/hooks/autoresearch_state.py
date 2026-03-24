#!/usr/bin/env python3

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from pathlib import Path


def load_payload() -> dict:
    try:
        return json.load(sys.stdin)
    except json.JSONDecodeError:
        return {}


def repo_root(payload: dict) -> Path:
    return Path(payload.get("cwd", ".")).resolve()


def run_git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    return result.stdout.strip()


def current_branch(root: Path) -> str:
    return run_git(root, "branch", "--show-current") or "unknown"


def has_merge_conflicts(root: Path) -> bool:
    output = run_git(root, "diff", "--name-only", "--diff-filter=U")
    return bool(output)


def provider_path(root: Path) -> Path:
    raw = os.environ.get("QLIB_PROVIDER_URI", str(root / "data/qlib_bin_daily_hfq"))
    return Path(raw).expanduser()


def provider_missing(root: Path) -> bool:
    provider = provider_path(root)
    required = [
        provider,
        provider / "calendars",
        provider / "features",
        provider / "instruments",
    ]
    return any(not path.exists() for path in required)


def allow_stop_requested(root: Path) -> bool:
    if os.environ.get("AUTORESEARCH_ALLOW_STOP") == "1":
        return True
    return (root / ".codex" / "allow_stop").exists()


def last_run(root: Path) -> dict:
    run_json = root / "run.json"
    if not run_json.exists():
        return {}
    try:
        return json.loads(run_json.read_text(encoding="utf-8"))
    except Exception:
        return {}


def ledger_counts(root: Path) -> dict[str, int]:
    path = root / "results.tsv"
    counts = {"total": 0, "keep": 0, "discard": 0, "crash": 0}
    if not path.exists():
        return counts
    try:
        with path.open(encoding="utf-8") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            for row in reader:
                counts["total"] += 1
                status = row.get("status", "")
                if status in counts:
                    counts[status] += 1
    except Exception:
        return counts
    return counts


def state_lines(root: Path) -> list[str]:
    lines = [f"Branch: {current_branch(root)}"]

    provider = provider_path(root)
    provider_state = "missing" if provider_missing(root) else "ok"
    lines.append(f"Provider: {provider} ({provider_state})")

    counts = ledger_counts(root)
    if counts["total"] > 0:
        lines.append(
            "Ledger: "
            f"total={counts['total']}, keep={counts['keep']}, "
            f"discard={counts['discard']}, crash={counts['crash']}"
        )

    summary = last_run(root)
    if summary:
        status = summary.get("status", "unknown")
        description = summary.get("description", "").strip() or "n/a"
        sharpe = summary.get("mean_sharpe")
        rank_ic = summary.get("mean_rank_ic")
        runtime = summary.get("runtime_seconds")
        metrics = []
        if sharpe is not None:
            metrics.append(f"sharpe={sharpe:.4f}")
        if rank_ic is not None:
            metrics.append(f"rank_ic={rank_ic:.4f}")
        if runtime is not None:
            metrics.append(f"runtime={runtime:.1f}s")
        suffix = f" ({', '.join(metrics)})" if metrics else ""
        lines.append(f"Last run: status={status}, description={description}{suffix}")

    return lines


def state_block(root: Path) -> str:
    return "\n".join(f"- {line}" for line in state_lines(root))
