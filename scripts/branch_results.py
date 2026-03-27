from __future__ import annotations

import csv
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_PROJECTION_SPECS = {
    "results.tsv": "results.tsv",
    "run.json": "run.json",
    "run_state.json": "run_state.json",
    "history.json": "tmp/codex_supervisor/history.json",
}


def git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def current_branch(repo_root: Path) -> str:
    return git(repo_root, "branch", "--show-current")


def current_head_commit(repo_root: Path) -> str:
    return git(repo_root, "rev-parse", "--short", "HEAD")


def supervisor_root(repo_root: Path) -> Path:
    return repo_root / "tmp" / "codex_supervisor"


def branch_slug(branch: str) -> str:
    return branch.replace("/", "__").replace(" ", "_")


def branch_from_slug(slug: str) -> str:
    return slug.replace("__", "/")


def branch_archive_dir(repo_root: Path, branch: str | None = None) -> Path:
    branch = branch or current_branch(repo_root)
    return supervisor_root(repo_root) / branch_slug(branch)


def branch_index_path(repo_root: Path) -> Path:
    return supervisor_root(repo_root) / "branch_index.json"


def root_projection_paths(repo_root: Path) -> dict[str, Path]:
    return {name: repo_root / relpath for name, relpath in ROOT_PROJECTION_SPECS.items()}


def archive_paths(repo_root: Path, branch: str | None = None) -> dict[str, Path]:
    archive_dir = branch_archive_dir(repo_root, branch)
    return {name: archive_dir / name for name in ROOT_PROJECTION_SPECS}


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_results(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def remove_if_exists(path: Path) -> None:
    if path.exists():
        path.unlink()


def copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def minimal_run_state(repo_root: Path, branch: str | None = None) -> dict[str, Any]:
    branch = branch or current_branch(repo_root)
    return {
        "version": 1,
        "branch": branch,
        "phase": "idle_uninitialized",
        "backtest_version": None,
        "latest_keep_commit": None,
        "latest_finalized_commit": None,
        "latest_finalized_status": None,
        "current_candidate_commit": None,
        "current_head_commit": current_head_commit(repo_root),
        "latest_description": None,
        "latest_category": None,
    }


def root_matches_branch(repo_root: Path, branch: str | None = None) -> bool:
    expected_head = current_head_commit(repo_root)
    state = read_json(root_projection_paths(repo_root)["run_state.json"])
    if state.get("current_head_commit") == expected_head:
        return True
    run_summary = read_json(root_projection_paths(repo_root)["run.json"])
    if run_summary.get("commit") == expected_head:
        return True
    return False


def clear_root_projection(repo_root: Path, branch: str | None = None) -> None:
    paths = root_projection_paths(repo_root)
    remove_if_exists(paths["results.tsv"])
    remove_if_exists(paths["run.json"])
    remove_if_exists(paths["history.json"])
    write_json(paths["run_state.json"], minimal_run_state(repo_root, branch))


def archive_exists(repo_root: Path, branch: str | None = None) -> bool:
    paths = archive_paths(repo_root, branch)
    return any(path.exists() for path in paths.values())


def legacy_run_paths(repo_root: Path, branch: str | None = None) -> list[Path]:
    archive_dir = branch_archive_dir(repo_root, branch)
    return sorted(archive_dir.glob("run_*.json"))


def row_from_run_summary(summary: dict[str, Any]) -> dict[str, str]:
    mean_sharpe = summary.get("mean_sharpe", "")
    return {
        "commit": str(summary.get("commit", "")),
        "backtest_version": str(summary.get("backtest_version") or "v1_legacy"),
        "sharpe": str(mean_sharpe),
        "external_sharpe": str(summary.get("mean_external_sharpe", mean_sharpe)),
        "raw_sharpe": str(summary.get("mean_raw_sharpe", mean_sharpe)),
        "rank_ic": str(summary.get("mean_rank_ic", "")),
        "turnover": str(summary.get("mean_turnover", "")),
        "max_drawdown": str(summary.get("mean_max_drawdown", "")),
        "status": str(summary.get("status", "")),
        "category": str(summary.get("llm_category") or ""),
        "baseline_commit": str(summary.get("baseline_commit") or ""),
        "experiment_fingerprint": str(summary.get("experiment_fingerprint") or ""),
        "description": str(summary.get("description", "")),
    }


def write_results_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "commit",
        "backtest_version",
        "sharpe",
        "external_sharpe",
        "raw_sharpe",
        "rank_ic",
        "turnover",
        "max_drawdown",
        "status",
        "category",
        "baseline_commit",
        "experiment_fingerprint",
        "description",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def bootstrap_archive_from_legacy_runs(repo_root: Path, branch: str | None = None) -> bool:
    branch = branch or current_branch(repo_root)
    archive = archive_paths(repo_root, branch)
    runs = legacy_run_paths(repo_root, branch)
    if not runs or archive["results.tsv"].exists():
        return False

    summaries = [read_json(path) for path in runs]
    rows = [row_from_run_summary(summary) for summary in summaries]
    write_results_rows(archive["results.tsv"], rows)

    latest = summaries[-1]
    keep_rows = [row for row in rows if row.get("status") == "keep"]
    latest_keep = keep_rows[-1] if keep_rows else None
    state = {
        "version": 1,
        "branch": branch,
        "phase": (
            "candidate_recorded"
            if latest.get("status") in {"candidate", "hard_reject"}
            else f"finalized_{latest.get('status', 'unknown')}"
        ),
        "backtest_version": latest.get("backtest_version") or "v1_legacy",
        "latest_keep_commit": latest_keep.get("commit") if latest_keep else None,
        "latest_finalized_commit": latest.get("commit"),
        "latest_finalized_status": latest.get("status"),
        "current_candidate_commit": latest.get("commit") if latest.get("status") in {"candidate", "hard_reject"} else None,
        "current_head_commit": latest.get("commit"),
        "latest_description": latest.get("description"),
        "latest_category": latest.get("llm_category") or latest.get("category"),
    }
    write_json(archive["run_state.json"], state)
    write_json(archive["run.json"], latest)
    return True


def discover_branches(repo_root: Path) -> list[str]:
    root = supervisor_root(repo_root)
    if not root.exists():
        return []
    branches: list[str] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        if child.name.startswith("."):
            continue
        branches.append(branch_from_slug(child.name))
    return sorted(set(branches))


def summarize_branch(repo_root: Path, branch: str | None = None) -> dict[str, Any]:
    branch = branch or current_branch(repo_root)
    archive = archive_paths(repo_root, branch)
    state = read_json(archive["run_state.json"])
    run_summary = read_json(archive["run.json"])
    rows = load_results(archive["results.tsv"])

    keep_rows = [row for row in rows if row.get("status") == "keep"]
    latest_keep = keep_rows[-1] if keep_rows else None
    latest_result = rows[-1] if rows else None
    counts = {"total": len(rows), "keep": 0, "discard": 0, "crash": 0}
    for row in rows:
        status = row.get("status", "")
        if status in counts:
            counts[status] += 1

    latest_backtest_version = (
        state.get("backtest_version")
        or run_summary.get("backtest_version")
        or (latest_result.get("backtest_version") if latest_result else None)
    )
    summary = {
        "branch": branch,
        "branch_slug": branch_slug(branch),
        "archive_dir": str(branch_archive_dir(repo_root, branch)),
        "backtest_version": latest_backtest_version,
        "latest_keep_commit": state.get("latest_keep_commit") or (latest_keep or {}).get("commit"),
        "latest_finalized_commit": state.get("latest_finalized_commit") or (latest_result or {}).get("commit"),
        "latest_finalized_status": state.get("latest_finalized_status") or (latest_result or {}).get("status"),
        "latest_description": state.get("latest_description") or (latest_result or {}).get("description"),
        "latest_category": state.get("latest_category") or (latest_result or {}).get("category"),
        "result_count": counts["total"],
        "keep_count": counts["keep"],
        "discard_count": counts["discard"],
        "crash_count": counts["crash"],
        "last_updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    if latest_keep is not None:
        summary["latest_keep"] = {
            "commit": latest_keep.get("commit"),
            "description": latest_keep.get("description"),
            "sharpe": latest_keep.get("sharpe"),
            "external_sharpe": latest_keep.get("external_sharpe"),
            "raw_sharpe": latest_keep.get("raw_sharpe"),
            "rank_ic": latest_keep.get("rank_ic"),
            "turnover": latest_keep.get("turnover"),
            "max_drawdown": latest_keep.get("max_drawdown"),
        }
    return summary


def update_branch_index(repo_root: Path, branch: str | None = None) -> dict[str, Any]:
    branch = branch or current_branch(repo_root)
    bootstrap_archive_from_legacy_runs(repo_root, branch)
    path = branch_index_path(repo_root)
    payload = read_json(path) if path.exists() else {"version": 1, "branches": []}
    payload.setdefault("version", 1)
    payload.setdefault("branches", [])
    summary = summarize_branch(repo_root, branch)
    branches = payload["branches"]
    existing = next((idx for idx, item in enumerate(branches) if item.get("branch") == branch), None)
    if existing is None:
        branches.append(summary)
    else:
        branches[existing] = summary
    branches.sort(key=lambda item: item.get("last_updated_at", ""), reverse=True)
    write_json(path, payload)
    write_json(branch_archive_dir(repo_root, branch) / "summary.json", summary)
    return summary


def sync_branch_state(repo_root: Path, branch: str | None = None) -> dict[str, Any]:
    branch = branch or current_branch(repo_root)
    root_paths = root_projection_paths(repo_root)
    branch_paths = archive_paths(repo_root, branch)
    archive_dir = branch_archive_dir(repo_root, branch)
    archive_dir.mkdir(parents=True, exist_ok=True)
    for name, root_path in root_paths.items():
        branch_path = branch_paths[name]
        if root_path.exists():
            copy_file(root_path, branch_path)
        else:
            remove_if_exists(branch_path)
    summary = update_branch_index(repo_root, branch)
    return {"branch": branch, "branch_slug": branch_slug(branch), "mode": "synced", "summary": summary}


def restore_branch_state(repo_root: Path, branch: str | None = None) -> dict[str, Any]:
    branch = branch or current_branch(repo_root)
    bootstrap_archive_from_legacy_runs(repo_root, branch)
    root_paths = root_projection_paths(repo_root)
    branch_paths = archive_paths(repo_root, branch)
    if archive_exists(repo_root, branch):
        for name, root_path in root_paths.items():
            branch_path = branch_paths[name]
            if branch_path.exists():
                copy_file(branch_path, root_path)
            else:
                remove_if_exists(root_path)
        summary = update_branch_index(repo_root, branch)
        return {"branch": branch, "branch_slug": branch_slug(branch), "mode": "restored", "summary": summary}

    if root_matches_branch(repo_root, branch):
        result = sync_branch_state(repo_root, branch)
        result["mode"] = "bootstrapped_from_root"
        return result

    clear_root_projection(repo_root, branch)
    result = sync_branch_state(repo_root, branch)
    result["mode"] = "initialized_empty"
    return result


def list_branch_summaries(repo_root: Path) -> list[dict[str, Any]]:
    payload = read_json(branch_index_path(repo_root))
    branches = {item.get("branch"): item for item in payload.get("branches", []) if item.get("branch")}
    for branch in discover_branches(repo_root):
        if branch not in branches:
            bootstrap_archive_from_legacy_runs(repo_root, branch)
            branches[branch] = summarize_branch(repo_root, branch)
    ordered = sorted(branches.values(), key=lambda item: item.get("last_updated_at", ""), reverse=True)
    write_json(branch_index_path(repo_root), {"version": 1, "branches": ordered})
    return ordered


def branch_summary(repo_root: Path, branch: str) -> dict[str, Any]:
    bootstrap_archive_from_legacy_runs(repo_root, branch)
    summary_path = branch_archive_dir(repo_root, branch) / "summary.json"
    if summary_path.exists():
        return read_json(summary_path)
    return summarize_branch(repo_root, branch)
