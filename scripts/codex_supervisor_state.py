#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import subprocess
from pathlib import Path


NOISE_EXACT = {"results.tsv", "run.json", "run.log"}
NOISE_PREFIXES = ("tmp/", ".vscode/")
LLM_CATEGORIES = {"factor", "label", "model", "strategy", "baseline", "other"}


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


def latest_result_row(results_path: Path) -> dict[str, str] | None:
    rows = load_results(results_path)
    return rows[-1] if rows else None


def load_run_summary(run_json_path: Path) -> dict:
    if not run_json_path.exists():
        raise SystemExit(f"run.json is missing at {run_json_path}")
    return json.loads(run_json_path.read_text(encoding="utf-8"))


def save_run_summary(run_json_path: Path, payload: dict) -> None:
    run_json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def normalize_llm_category(category: str) -> str:
    normalized = category.strip().lower()
    if normalized not in LLM_CATEGORIES:
        allowed = ", ".join(sorted(LLM_CATEGORIES))
        raise SystemExit(f"Unsupported category: {category}. Allowed: {allowed}")
    return normalized


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


def resolve_latest_category(repo_root: Path, latest: dict[str, str]) -> tuple[str, str]:
    run_json_path = repo_root / "run.json"
    if run_json_path.exists():
        run_summary = load_run_summary(run_json_path)
        if str(run_summary.get("commit", "")) == latest["commit"]:
            llm_category = run_summary.get("llm_category")
            if isinstance(llm_category, str) and llm_category.strip():
                return normalize_llm_category(llm_category), "llm"
    return classify_experiment(repo_root, latest["description"], latest["commit"]), "heuristic"


def history_path(repo_root: Path) -> Path:
    return repo_root / "tmp" / "codex_supervisor" / "history.json"


def bootstrap_history(repo_root: Path) -> dict:
    entries = []
    for row in load_results(repo_root / "results.tsv"):
        category = classify_experiment(repo_root, row["description"], row["commit"])
        if category == "baseline":
            continue
        valid = row["status"] in {"keep", "discard"} and category != "baseline"
        entries.append(
            {
                "commit": row["commit"],
                "description": row["description"],
                "status": row["status"],
                "category": category,
                "valid": valid,
                "valid_reason": "bootstrap" if valid else f"bootstrap_{row['status']}",
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


def cmd_finalize_result(repo_root: Path, decision: str, reason: str, category: str | None) -> None:
    results_path = repo_root / "results.tsv"
    latest = latest_result_row(results_path)
    if latest is None:
        raise SystemExit("results.tsv is empty; cannot finalize result")

    if decision not in {"keep", "discard"}:
        raise SystemExit(f"Unsupported decision: {decision}")
    if not category:
        raise SystemExit("--category is required for finalize-result")
    normalized_category = normalize_llm_category(category)

    latest_status = latest["status"]
    if latest_status == "crash":
        raise SystemExit("Crash results are already final; do not finalize them again")
    if latest_status == "hard_reject" and decision != "discard":
        raise SystemExit("hard_reject results can only be finalized as discard")
    if latest_status in {"keep", "discard"} and latest_status != decision:
        raise SystemExit(f"Latest result is already finalized as {latest_status}")

    rewrite_latest_result_status(results_path, latest["commit"], decision)

    run_summary = load_run_summary(repo_root / "run.json")
    prior_status = str(run_summary.get("status", latest_status))
    run_summary["harness_status"] = run_summary.get("harness_status") or prior_status
    run_summary["status"] = decision
    run_summary["llm_decision"] = decision
    run_summary["llm_decision_reason"] = reason.strip()
    run_summary["llm_category"] = normalized_category
    save_run_summary(repo_root / "run.json", run_summary)

    print(
        json.dumps(
            {
                "commit": latest["commit"],
                "previous_status": latest_status,
                "status": decision,
                "category": normalized_category,
                "reason": reason.strip(),
            }
        )
    )


def cmd_record_result(repo_root: Path, required: str | None) -> None:
    history = load_or_bootstrap_history(repo_root)
    results_path = repo_root / "results.tsv"
    rows = load_results(results_path)
    if not rows:
        raise SystemExit("results.tsv is empty; cannot record result")

    latest = rows[-1]
    if latest["status"] in {"candidate", "hard_reject"}:
        print(
            json.dumps(
                {
                    "commit": latest["commit"],
                    "description": latest["description"],
                    "status": latest["status"],
                    "valid": False,
                    "valid_reason": "unfinalized_status",
                }
            )
        )
        raise SystemExit(2)

    category, category_source = resolve_latest_category(repo_root, latest)
    valid = latest["status"] in {"keep", "discard"} and category != "baseline"
    entry = {
        "commit": latest["commit"],
        "description": latest["description"],
        "status": latest["status"],
        "category": category,
        "category_source": category_source,
        "valid": valid,
        "valid_reason": (
            "ok"
            if valid
            else ("crash" if latest["status"] == "crash" else "baseline_category")
        ),
    }

    if required is not None:
        entry["required_category"] = required

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
    parser.add_argument("command", choices=["preflight", "finalize-result", "record-result"])
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--required-category")
    parser.add_argument("--decision")
    parser.add_argument("--category")
    parser.add_argument("--reason", default="")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()

    if args.command == "preflight":
        cmd_preflight(repo_root)
        return 0
    if args.command == "finalize-result":
        if not args.decision:
            raise SystemExit("--decision is required for finalize-result")
        cmd_finalize_result(repo_root, args.decision, args.reason, args.category)
        return 0
    if args.command == "record-result":
        cmd_record_result(repo_root, args.required_category)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
