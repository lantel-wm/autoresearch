#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import subprocess
from collections import Counter
from pathlib import Path


NOISE_EXACT = {"results.tsv", "run.json", "run.log"}
NOISE_PREFIXES = ("tmp/", ".vscode/")


def git(repo_root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=check,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def load_results(results_path: Path) -> list[dict[str, str]]:
    if not results_path.exists():
        return []
    with results_path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def rewrite_latest_result_status(results_path: Path, commit: str, status: str) -> None:
    rows = load_results(results_path)
    if not rows or rows[-1]["commit"] != commit:
        return
    rows[-1]["status"] = status
    fieldnames = rows[0].keys()
    with results_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def latest_keep_row(results_path: Path) -> dict[str, str] | None:
    rows = load_results(results_path)
    keeps = [row for row in rows if row["status"] == "keep"]
    return keeps[-1] if keeps else None


def normalize_status_path(path: str) -> str:
    return path.split(" -> ", 1)[-1]


def is_noise_path(path: str) -> bool:
    normalized = path.lstrip("./")
    if normalized in NOISE_EXACT:
        return True
    if any(normalized.startswith(prefix) for prefix in NOISE_PREFIXES):
        return True
    if normalized.endswith(".swp"):
        return True
    return False


def tracked_and_untracked_changes(repo_root: Path) -> tuple[list[str], list[str]]:
    lines = git(repo_root, "status", "--porcelain=v1", "--untracked-files=all", check=False).splitlines()
    tracked: list[str] = []
    untracked: list[str] = []
    for line in lines:
        if not line:
            continue
        status = line[:2]
        path = normalize_status_path(line[3:])
        if is_noise_path(path):
            continue
        if status == "??":
            untracked.append(path)
        else:
            tracked.append(path)
    return tracked, untracked


def get_commit_subject(repo_root: Path, commit: str) -> str:
    try:
        return git(repo_root, "show", "-s", "--format=%s", commit)
    except subprocess.CalledProcessError:
        return ""


def classify_experiment(repo_root: Path, description: str, commit: str) -> str:
    description_lower = description.lower()
    if description_lower.startswith("baseline"):
        return "baseline"
    for source in (description_lower, get_commit_subject(repo_root, commit).lower()):
        for category in ("factor", "label", "model", "strategy"):
            if f"[{category}]" in source:
                return category
        for category in ("factor", "label", "model", "strategy"):
            if source.startswith(f"{category}_"):
                return category
    return "other"


def history_path(repo_root: Path) -> Path:
    return repo_root / "tmp" / "codex_supervisor" / "history.json"


def bootstrap_history(repo_root: Path) -> dict:
    entries = []
    for row in load_results(repo_root / "results.tsv"):
        category = classify_experiment(repo_root, row["description"], row["commit"])
        if category == "baseline":
            continue
        valid = row["status"] != "crash" and category in {"factor", "label", "model", "strategy"}
        entries.append(
            {
                "commit": row["commit"],
                "description": row["description"],
                "status": row["status"],
                "category": category,
                "valid": valid,
                "valid_reason": "bootstrap" if valid else "bootstrap_crash",
            }
        )
    return {"version": 1, "entries": entries}


def load_or_bootstrap_history(repo_root: Path) -> dict:
    path = history_path(repo_root)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    history = bootstrap_history(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return history


def save_history(repo_root: Path, history: dict) -> None:
    path = history_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def valid_window(history: dict) -> list[dict]:
    valid_entries = [
        entry
        for entry in history["entries"]
        if entry.get("valid") and entry.get("category") in {"factor", "label", "model", "strategy"}
    ]
    return valid_entries[-9:]


def required_category(history: dict) -> dict:
    window = valid_window(history)
    counts = Counter(entry["category"] for entry in window)
    remaining_after_this = 10 - (len(window) + 1)

    if counts["factor"] + remaining_after_this < 6:
        required = "factor"
        reason = "factor_quota"
    elif counts["label"] + remaining_after_this < 2:
        required = "label"
        reason = "label_quota"
    else:
        required = "factor"
        reason = "default_factor"

    return {
        "required_category": required,
        "reason": reason,
        "window_size": len(window),
        "counts": dict(counts),
    }


def restore_train(repo_root: Path, keep_commit: str) -> bool:
    current = (repo_root / "train.py").read_text(encoding="utf-8")
    keep_content = git(repo_root, "show", f"{keep_commit}:train.py")
    if current == keep_content:
        return False
    (repo_root / "train.py").write_text(keep_content, encoding="utf-8")
    git(repo_root, "add", "train.py")
    git(repo_root, "commit", "-m", "Restore kept train baseline")
    return True


def cmd_preflight(repo_root: Path) -> None:
    tracked, untracked = tracked_and_untracked_changes(repo_root)
    keep = latest_keep_row(repo_root / "results.tsv")
    if keep is None:
        print(json.dumps({"ok": True, "reason": "no_baseline", "restored_train": False}))
        return

    if untracked:
        print(
            json.dumps(
                {
                    "ok": False,
                    "reason": "dirty_untracked",
                    "details": sorted(untracked),
                    "latest_keep_commit": keep["commit"],
                }
            )
        )
        return

    if tracked and set(tracked) != {"train.py"}:
        print(
            json.dumps(
                {
                    "ok": False,
                    "reason": "dirty_tracked",
                    "details": sorted(tracked),
                    "latest_keep_commit": keep["commit"],
                }
            )
        )
        return

    restored = restore_train(repo_root, keep["commit"])
    print(
        json.dumps(
            {
                "ok": True,
                "reason": "restored_train" if restored else "clean",
                "restored_train": restored,
                "latest_keep_commit": keep["commit"],
            }
        )
    )


def cmd_required_category(repo_root: Path) -> None:
    history = load_or_bootstrap_history(repo_root)
    print(json.dumps(required_category(history)))


def cmd_record_result(repo_root: Path, required: str) -> None:
    history = load_or_bootstrap_history(repo_root)
    results_path = repo_root / "results.tsv"
    rows = load_results(results_path)
    if not rows:
        raise SystemExit("results.tsv is empty; cannot record result")

    latest = rows[-1]
    category = classify_experiment(repo_root, latest["description"], latest["commit"])
    valid = latest["status"] != "crash" and category == required
    entry = {
        "commit": latest["commit"],
        "description": latest["description"],
        "status": latest["status"],
        "category": category,
        "required_category": required,
        "valid": valid,
        "valid_reason": (
            "ok"
            if valid
            else ("crash" if latest["status"] == "crash" else "invalid_category")
        ),
    }

    if not valid and latest["status"] == "keep":
        rewrite_latest_result_status(results_path, latest["commit"], "discard")
        entry["status"] = "discard"

    entries = history["entries"]
    existing = next((i for i, item in enumerate(entries) if item["commit"] == latest["commit"]), None)
    if existing is None:
        entries.append(entry)
    else:
        entries[existing] = entry
    save_history(repo_root, history)
    print(json.dumps(entry))


def main() -> int:
    parser = argparse.ArgumentParser(description="State helpers for autoresearch supervisor.")
    parser.add_argument("command", choices=["preflight", "required-category", "record-result"])
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--required-category")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()

    if args.command == "preflight":
        cmd_preflight(repo_root)
        return 0
    if args.command == "required-category":
        cmd_required_category(repo_root)
        return 0
    if args.command == "record-result":
        if not args.required_category:
            raise SystemExit("--required-category is required for record-result")
        cmd_record_result(repo_root, args.required_category)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
