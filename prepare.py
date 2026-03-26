"""
Fixed Qlib harness for autoresearch-style quant experiments.

`prepare.py` is read-only during the experiment loop. It owns:
- provider validation
- fold definitions
- data loading from the local Qlib provider
- model fitting and backtest evaluation
- run summary serialization
- baseline-relative metrics and last-resort hard rejects
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
import qlib
from qlib.constant import REG_CN
from qlib.data import D

# Keep matplotlib and similar libraries from trying to write to ~/.matplotlib.
DEFAULT_MPLCONFIGDIR = Path("tmp/mplconfig").resolve()
os.environ.setdefault("MPLCONFIGDIR", str(DEFAULT_MPLCONFIGDIR))
Path(os.environ["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)

TIME_BUDGET_SECONDS = 600
DEFAULT_PROVIDER_URI = Path("data/qlib_bin_daily_hfq")
DEFAULT_MARKET = "ashare_mainboard_no_st"
DEFAULT_BENCHMARK = "SH000300"

DEFAULT_TOPK = 50
DEFAULT_N_DROP = 5
OPEN_COST = 0.0005
CLOSE_COST = 0.0015
MIN_COST = 5.0
ACCOUNT_SIZE = 1_000_000.0
HARD_REJECT_TURNOVER_RATIO = 1.60
HARD_REJECT_DRAWDOWN_RATIO = 1.35

RESULTS_TSV_PATH = Path("results.tsv")
RUN_JSON_PATH = Path("run.json")

TRADE_RETURN_EXPR = "Ref($open, -2) / Ref($open, -1) - 1"
RESULTS_HEADER = [
    "commit",
    "sharpe",
    "rank_ic",
    "turnover",
    "max_drawdown",
    "status",
    "description",
]

_QLIB_INITIALIZED_URI: str | None = None


@dataclass(frozen=True)
class Fold:
    name: str
    train_start: str
    train_end: str
    valid_start: str
    valid_end: str
    test_start: str
    test_end: str


@dataclass
class ExperimentSpec:
    description: str
    feature_expressions: list[tuple[str, str]]
    label_expression: str
    model_type: str = "lgbm"
    model_kwargs: dict[str, Any] = field(default_factory=dict)
    strategy_kwargs: dict[str, Any] = field(
        default_factory=lambda: {"topk": DEFAULT_TOPK, "n_drop": DEFAULT_N_DROP}
    )
    seed: int = 42


@dataclass
class FoldMetrics:
    fold: str
    sharpe: float
    rank_ic: float
    turnover: float
    max_drawdown: float
    annual_return: float
    num_days: int


@dataclass
class RunSummary:
    commit: str
    description: str
    status: str
    mean_sharpe: float
    mean_rank_ic: float
    mean_turnover: float
    mean_max_drawdown: float
    mean_annual_return: float
    runtime_seconds: float
    provider_uri: str
    market: str
    harness_status: str | None = None
    hard_reject: bool | None = None
    hard_reject_reason: str | None = None
    decision_reason: str | None = None
    baseline_commit: str | None = None
    baseline_description: str | None = None
    baseline_sharpe: float | None = None
    baseline_rank_ic: float | None = None
    baseline_turnover: float | None = None
    baseline_max_drawdown: float | None = None
    sharpe_delta: float | None = None
    rank_ic_delta: float | None = None
    turnover_ratio: float | None = None
    max_drawdown_ratio: float | None = None
    llm_decision: str | None = None
    llm_decision_reason: str | None = None
    folds: list[FoldMetrics] = field(default_factory=list)
    error: str | None = None


ROLLING_FOLDS = [
    Fold("fold_2021", "2015-01-01", "2019-12-31", "2020-01-01", "2020-12-31", "2021-01-01", "2021-12-31"),
    Fold("fold_2022", "2016-01-01", "2020-12-31", "2021-01-01", "2021-12-31", "2022-01-01", "2022-12-31"),
    Fold("fold_2023", "2017-01-01", "2021-12-31", "2022-01-01", "2022-12-31", "2023-01-01", "2023-12-31"),
    Fold("fold_2024", "2018-01-01", "2022-12-31", "2023-01-01", "2023-12-31", "2024-01-01", "2024-12-31"),
    Fold("fold_2025", "2019-01-01", "2023-12-31", "2024-01-01", "2024-12-31", "2025-01-01", "2025-12-31"),
]


def get_provider_uri() -> Path:
    return Path(os.environ.get("QLIB_PROVIDER_URI", str(DEFAULT_PROVIDER_URI))).expanduser()


def require_provider(provider_uri: Path) -> None:
    required_paths = [
        provider_uri,
        provider_uri / "calendars",
        provider_uri / "features",
        provider_uri / "instruments",
    ]
    missing = [str(path) for path in required_paths if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Qlib provider is missing. Expected local provider at "
            f"{provider_uri}. Missing paths: {', '.join(missing)}"
        )


def init_qlib(provider_uri: Path) -> None:
    global _QLIB_INITIALIZED_URI
    provider_uri_str = str(provider_uri.resolve())
    if _QLIB_INITIALIZED_URI == provider_uri_str:
        return
    qlib.init(
        provider_uri=provider_uri_str,
        region=REG_CN,
        expression_cache=None,
        dataset_cache=None,
        default_disk_cache=1,
    )
    _QLIB_INITIALIZED_URI = provider_uri_str


def check_provider(provider_uri: Path) -> None:
    require_provider(provider_uri)
    init_qlib(provider_uri)

    instruments = D.instruments(DEFAULT_MARKET)
    sample = D.features(
        instruments,
        ["$open", "$close", "$volume", "$turnover_rate"],
        start_time=ROLLING_FOLDS[-1].test_start,
        end_time=ROLLING_FOLDS[-1].test_end,
    )
    sample = normalize_feature_frame(sample)
    if sample.empty:
        raise RuntimeError(
            "Provider check passed structurally, but querying sample features returned no rows. "
            f"Check market '{DEFAULT_MARKET}' and date coverage."
        )


def validate_experiment_spec(spec: ExperimentSpec) -> None:
    if not spec.description.strip():
        raise ValueError("Experiment description must be non-empty.")
    if "\t" in spec.description or "\n" in spec.description:
        raise ValueError("Experiment description must be a single TSV-safe line.")
    if spec.model_type != "lgbm":
        raise ValueError("Only model_type='lgbm' is supported in v1.")
    if not spec.feature_expressions:
        raise ValueError("At least one feature expression is required.")

    aliases = [alias for _, alias in spec.feature_expressions]
    if len(set(aliases)) != len(aliases):
        raise ValueError("Feature aliases must be unique.")
    if any(alias in {"label", "trade_return"} for alias in aliases):
        raise ValueError("Feature aliases cannot reuse reserved names: label, trade_return.")

    all_expressions = [expr for expr, _ in spec.feature_expressions] + [spec.label_expression]
    if any("vwap" in expr.lower() for expr in all_expressions):
        raise ValueError("This workflow does not support vwap-based expressions.")

    strategy = spec.strategy_kwargs
    if int(strategy.get("topk", DEFAULT_TOPK)) <= 0:
        raise ValueError("strategy_kwargs.topk must be positive.")
    if int(strategy.get("n_drop", DEFAULT_N_DROP)) < 0:
        raise ValueError("strategy_kwargs.n_drop must be non-negative.")


def normalize_feature_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(frame.index, pd.MultiIndex) or frame.index.nlevels != 2:
        raise RuntimeError("Expected Qlib feature frame with a 2-level MultiIndex.")

    normalized = frame.copy()
    level0 = normalized.index.get_level_values(0)
    if np.issubdtype(level0.dtype, np.datetime64):
        normalized = normalized.reorder_levels([1, 0])
    normalized.index = normalized.index.set_names(["instrument", "datetime"])
    normalized = normalized.sort_index()
    return normalized


def slice_dates(frame: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    dates = frame.index.get_level_values("datetime")
    mask = (dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))
    return frame.loc[mask].copy()


def fetch_panel(spec: ExperimentSpec) -> pd.DataFrame:
    provider_uri = get_provider_uri()
    require_provider(provider_uri)
    init_qlib(provider_uri)

    fields = [expr for expr, _ in spec.feature_expressions]
    aliases = [alias for _, alias in spec.feature_expressions]
    fields.extend([spec.label_expression, TRADE_RETURN_EXPR])
    aliases.extend(["label", "trade_return"])

    start_time = min(fold.train_start for fold in ROLLING_FOLDS)
    end_time = max(fold.test_end for fold in ROLLING_FOLDS)

    raw = D.features(D.instruments(DEFAULT_MARKET), fields, start_time=start_time, end_time=end_time)
    frame = normalize_feature_frame(raw)
    frame.columns = aliases
    frame = frame.replace([np.inf, -np.inf], np.nan)
    return frame


def build_model(spec: ExperimentSpec) -> lgb.LGBMRegressor:
    kwargs = {
        "objective": "regression",
        "n_estimators": 300,
        "learning_rate": 0.05,
        "num_leaves": 64,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "min_child_samples": 100,
        "reg_alpha": 0.0,
        "reg_lambda": 1.0,
        "random_state": spec.seed,
        "n_jobs": -1,
        "verbosity": -1,
    }
    kwargs.update(spec.model_kwargs)
    return lgb.LGBMRegressor(**kwargs)


def ensure_time_budget(start_time: float) -> None:
    elapsed = time.perf_counter() - start_time
    if elapsed > TIME_BUDGET_SECONDS:
        raise TimeoutError(f"Experiment exceeded {TIME_BUDGET_SECONDS} seconds.")


def run_fold(spec: ExperimentSpec, panel: pd.DataFrame, fold: Fold, start_time: float) -> FoldMetrics:
    ensure_time_budget(start_time)

    feature_names = [alias for _, alias in spec.feature_expressions]
    train_frame = slice_dates(panel, fold.train_start, fold.train_end)
    valid_frame = slice_dates(panel, fold.valid_start, fold.valid_end)
    test_frame = slice_dates(panel, fold.test_start, fold.test_end)

    train_frame = train_frame.dropna(subset=["label"])
    valid_frame = valid_frame.dropna(subset=["label"])
    test_frame = test_frame.dropna(subset=["label", "trade_return"])
    if train_frame.empty or valid_frame.empty or test_frame.empty:
        raise RuntimeError(f"{fold.name} has empty train/valid/test slices.")

    model = build_model(spec)
    fit_kwargs: dict[str, Any] = {
        "X": train_frame[feature_names],
        "y": train_frame["label"],
        "eval_set": [(valid_frame[feature_names], valid_frame["label"])],
        "eval_metric": "l2",
        "callbacks": [lgb.early_stopping(50, verbose=False), lgb.log_evaluation(period=0)],
    }
    model.fit(**fit_kwargs)
    ensure_time_budget(start_time)

    model_iteration = getattr(model, "best_iteration_", None) or getattr(model, "n_estimators_", None)
    predictions = model.predict(test_frame[feature_names], num_iteration=model_iteration)
    scored = test_frame.copy()
    scored["score"] = predictions

    rank_ic_series = scored.groupby(level="datetime").apply(compute_daily_rank_ic)
    daily_returns, turnovers = run_topk_dropout_backtest(
        scored,
        topk=int(spec.strategy_kwargs.get("topk", DEFAULT_TOPK)),
        n_drop=int(spec.strategy_kwargs.get("n_drop", DEFAULT_N_DROP)),
    )

    return FoldMetrics(
        fold=fold.name,
        sharpe=compute_sharpe(daily_returns),
        rank_ic=float(rank_ic_series.mean(skipna=True) or 0.0),
        turnover=float(turnovers.mean() or 0.0) if not turnovers.empty else 0.0,
        max_drawdown=compute_max_drawdown(daily_returns),
        annual_return=compute_annual_return(daily_returns),
        num_days=int(len(daily_returns)),
    )


def compute_daily_rank_ic(day_frame: pd.DataFrame) -> float:
    usable = day_frame.dropna(subset=["score", "label"])
    if len(usable) < 2:
        return float("nan")
    if usable["score"].nunique() < 2 or usable["label"].nunique() < 2:
        return float("nan")
    return float(usable["score"].corr(usable["label"], method="spearman"))


def run_topk_dropout_backtest(
    frame: pd.DataFrame,
    *,
    topk: int,
    n_drop: int,
) -> tuple[pd.Series, pd.Series]:
    records = frame.reset_index()[["instrument", "datetime", "score", "trade_return"]]
    holdings: list[str] = []
    previous_weights = pd.Series(dtype=float)
    daily_returns: list[tuple[pd.Timestamp, float]] = []
    turnovers: list[tuple[pd.Timestamp, float]] = []

    for date, day in records.groupby("datetime", sort=True):
        day = day.replace([np.inf, -np.inf], np.nan).dropna(subset=["score", "trade_return"])
        if day.empty:
            continue
        day = day.sort_values(["score", "instrument"], ascending=[False, True])
        available_scores = dict(zip(day["instrument"], day["score"]))

        if not holdings:
            target_holdings = day["instrument"].head(topk).tolist()
        else:
            weakest_holdings = sorted(holdings, key=lambda inst: available_scores.get(inst, -np.inf))
            sell_candidates = weakest_holdings[: min(n_drop, len(weakest_holdings))]
            target_holdings = [
                instrument
                for instrument in holdings
                if instrument not in sell_candidates and instrument in available_scores
            ]
            for instrument in day["instrument"]:
                if instrument not in target_holdings:
                    target_holdings.append(instrument)
                if len(target_holdings) >= topk:
                    break

        if not target_holdings:
            continue

        day_by_instrument = day.set_index("instrument")
        target_holdings = [instrument for instrument in target_holdings if instrument in day_by_instrument.index]
        if not target_holdings:
            continue

        weight = 1.0 / len(target_holdings)
        current_weights = pd.Series(weight, index=target_holdings, dtype=float)
        turnover = 0.5 * current_weights.sub(previous_weights, fill_value=0.0).abs().sum()

        buy_weight = current_weights.sub(previous_weights, fill_value=0.0).clip(lower=0.0).sum()
        sell_weight = previous_weights.sub(current_weights, fill_value=0.0).clip(lower=0.0).sum()
        cost = buy_weight * OPEN_COST + sell_weight * CLOSE_COST
        if buy_weight > 0:
            cost += MIN_COST / ACCOUNT_SIZE
        if sell_weight > 0:
            cost += MIN_COST / ACCOUNT_SIZE

        gross_return = float(day_by_instrument.loc[target_holdings, "trade_return"].mean())
        net_return = gross_return - cost
        daily_returns.append((pd.Timestamp(date), net_return))
        turnovers.append((pd.Timestamp(date), float(turnover)))

        holdings = list(current_weights.index)
        previous_weights = current_weights

    return (
        pd.Series({date: value for date, value in daily_returns}, dtype=float).sort_index(),
        pd.Series({date: value for date, value in turnovers}, dtype=float).sort_index(),
    )


def compute_sharpe(returns: pd.Series) -> float:
    if len(returns) < 2:
        return 0.0
    std = float(returns.std(ddof=1))
    if std <= 1e-12:
        return 0.0
    return float(math.sqrt(252.0) * returns.mean() / std)


def compute_annual_return(returns: pd.Series) -> float:
    if returns.empty:
        return 0.0
    equity = (1.0 + returns).cumprod()
    total_days = len(returns)
    if total_days == 0 or equity.iloc[-1] <= 0:
        return 0.0
    return float(equity.iloc[-1] ** (252.0 / total_days) - 1.0)


def compute_max_drawdown(returns: pd.Series) -> float:
    if returns.empty:
        return 0.0
    equity = (1.0 + returns).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    return float(abs(drawdown.min()))


def fold_recency_weights(folds: list[FoldMetrics]) -> np.ndarray:
    # Later rolling test years get higher weight than older ones.
    return np.arange(1, len(folds) + 1, dtype=float)


def recency_weighted_mean(values: list[float], folds: list[FoldMetrics]) -> float:
    if not folds:
        return 0.0
    weights = fold_recency_weights(folds)
    return float(np.average(values, weights=weights))


def aggregate_summary(
    *,
    spec: ExperimentSpec,
    folds: list[FoldMetrics],
    runtime_seconds: float,
    status: str,
    error: str | None = None,
) -> RunSummary:
    return RunSummary(
        commit=current_commit_hash(),
        description=spec.description,
        status=status,
        harness_status=status,
        mean_sharpe=recency_weighted_mean([fold.sharpe for fold in folds], folds),
        mean_rank_ic=recency_weighted_mean([fold.rank_ic for fold in folds], folds),
        mean_turnover=recency_weighted_mean([fold.turnover for fold in folds], folds),
        mean_max_drawdown=recency_weighted_mean([fold.max_drawdown for fold in folds], folds),
        mean_annual_return=recency_weighted_mean([fold.annual_return for fold in folds], folds),
        runtime_seconds=runtime_seconds,
        provider_uri=str(get_provider_uri()),
        market=DEFAULT_MARKET,
        folds=folds,
        error=error,
    )


def current_commit_hash() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def load_current_baseline() -> dict[str, float | str] | None:
    if not RESULTS_TSV_PATH.exists() or RESULTS_TSV_PATH.stat().st_size == 0:
        return None

    results = pd.read_csv(RESULTS_TSV_PATH, sep="\t")
    kept = results[results["status"] == "keep"]
    if kept.empty:
        return None

    latest = kept.iloc[-1]
    return {
        "commit": str(latest["commit"]),
        "description": str(latest["description"]),
        "sharpe": float(latest["sharpe"]),
        "rank_ic": float(latest["rank_ic"]),
        "turnover": float(latest["turnover"]),
        "max_drawdown": float(latest["max_drawdown"]),
    }


def compute_ratio(current: float, baseline: float) -> float | None:
    if baseline <= 0:
        return None
    return current / baseline


def evaluate_harness_status(summary: RunSummary) -> str:
    baseline = load_current_baseline()
    if baseline is None:
        summary.decision_reason = "no_baseline"
        summary.hard_reject = False
        return "candidate"

    summary.baseline_commit = str(baseline["commit"])
    summary.baseline_description = str(baseline["description"])
    summary.baseline_sharpe = baseline["sharpe"]
    summary.baseline_rank_ic = baseline["rank_ic"]
    summary.baseline_turnover = baseline["turnover"]
    summary.baseline_max_drawdown = baseline["max_drawdown"]
    summary.sharpe_delta = summary.mean_sharpe - baseline["sharpe"]
    summary.rank_ic_delta = summary.mean_rank_ic - baseline["rank_ic"]
    summary.turnover_ratio = compute_ratio(summary.mean_turnover, baseline["turnover"])
    summary.max_drawdown_ratio = compute_ratio(summary.mean_max_drawdown, baseline["max_drawdown"])

    if summary.mean_rank_ic <= 0.0:
        summary.hard_reject = True
        summary.hard_reject_reason = "rankic_nonpositive"
        summary.decision_reason = "hard_reject"
        return "hard_reject"
    if summary.turnover_ratio is not None and summary.turnover_ratio > HARD_REJECT_TURNOVER_RATIO:
        summary.hard_reject = True
        summary.hard_reject_reason = "turnover_extreme"
        summary.decision_reason = "hard_reject"
        return "hard_reject"
    if summary.max_drawdown_ratio is not None and summary.max_drawdown_ratio > HARD_REJECT_DRAWDOWN_RATIO:
        summary.hard_reject = True
        summary.hard_reject_reason = "drawdown_extreme"
        summary.decision_reason = "hard_reject"
        return "hard_reject"

    summary.hard_reject = False
    summary.decision_reason = "llm_decision_required"
    return "candidate"


def ensure_results_header() -> None:
    if RESULTS_TSV_PATH.exists():
        return
    RESULTS_TSV_PATH.write_text("\t".join(RESULTS_HEADER) + "\n", encoding="utf-8")


def append_results_tsv(summary: RunSummary) -> None:
    ensure_results_header()
    row = [
        summary.commit,
        f"{summary.mean_sharpe:.6f}",
        f"{summary.mean_rank_ic:.6f}",
        f"{summary.mean_turnover:.6f}",
        f"{summary.mean_max_drawdown:.6f}",
        summary.status,
        sanitize_tsv_field(summary.description),
    ]
    with RESULTS_TSV_PATH.open("a", encoding="utf-8") as handle:
        handle.write("\t".join(row) + "\n")


def sanitize_tsv_field(value: str) -> str:
    return value.replace("\t", " ").replace("\n", " ").strip()


def write_run_json(summary: RunSummary) -> None:
    RUN_JSON_PATH.write_text(json.dumps(asdict(summary), indent=2, ensure_ascii=False), encoding="utf-8")


def print_summary(summary: RunSummary) -> None:
    print("---")
    print(f"status:           {summary.status}")
    if summary.decision_reason:
        print(f"decision_reason:  {summary.decision_reason}")
    if summary.hard_reject is not None:
        print(f"hard_reject:      {str(summary.hard_reject).lower()}")
    if summary.hard_reject_reason:
        print(f"hard_reject_reason:{summary.hard_reject_reason}")
    print(f"mean_sharpe:      {summary.mean_sharpe:.6f}")
    print(f"mean_rank_ic:     {summary.mean_rank_ic:.6f}")
    print(f"mean_turnover:    {summary.mean_turnover:.6f}")
    print(f"mean_max_drawdown:{summary.mean_max_drawdown:.6f}")
    print(f"mean_annual_return:{summary.mean_annual_return:.6f}")
    print(f"runtime_seconds:  {summary.runtime_seconds:.1f}")
    print(f"description:      {summary.description}")
    if summary.error:
        print(f"error:            {summary.error}")


def run_experiment(spec: ExperimentSpec) -> RunSummary:
    validate_experiment_spec(spec)
    start_time = time.perf_counter()
    folds: list[FoldMetrics] = []

    try:
        provider_uri = get_provider_uri()
        check_provider(provider_uri)
        panel = fetch_panel(spec)
        for fold in ROLLING_FOLDS:
            folds.append(run_fold(spec, panel, fold, start_time))

        runtime_seconds = time.perf_counter() - start_time
        summary = aggregate_summary(
            spec=spec,
            folds=folds,
            runtime_seconds=runtime_seconds,
            status="candidate",
        )
        summary.harness_status = evaluate_harness_status(summary)
        summary.status = summary.harness_status
    except Exception as exc:
        runtime_seconds = time.perf_counter() - start_time
        error = f"{exc.__class__.__name__}: {exc}"
        print(traceback.format_exc(), file=sys.stderr)
        summary = aggregate_summary(
            spec=spec,
            folds=folds,
            runtime_seconds=runtime_seconds,
            status="crash",
            error=error,
        )
        summary.hard_reject = False

    write_run_json(summary)
    append_results_tsv(summary)
    print_summary(summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Fixed Qlib harness for autoresearch-style quant experiments.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate the local provider and exit.",
    )
    args = parser.parse_args()

    if args.check:
        try:
            check_provider(get_provider_uri())
        except Exception as exc:
            print(f"provider_check: failed ({exc})", file=sys.stderr)
            return 1
        print(f"provider_check: ok ({get_provider_uri()})")
        return 0

    print("prepare.py is the fixed harness. Run train.py to execute an experiment.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
