"""
Fixed Qlib harness for autoresearch-style quant experiments.

`prepare.py` is read-only during the experiment loop. It owns:
- provider validation
- fold definitions
- data loading from the local Qlib provider
- model fitting and Qlib-backed backtest evaluation
- run summary serialization
- baseline-relative metrics and last-resort hard rejects
"""

from __future__ import annotations

import argparse
import csv
import hashlib
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
from qlib.contrib.evaluate import backtest_daily
from qlib.contrib.strategy import TopkDropoutStrategy
from qlib.data import D

# Keep matplotlib and similar libraries from trying to write to ~/.matplotlib.
DEFAULT_MPLCONFIGDIR = Path("tmp/mplconfig").resolve()
os.environ.setdefault("MPLCONFIGDIR", str(DEFAULT_MPLCONFIGDIR))
Path(os.environ["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)

TIME_BUDGET_SECONDS = 600
BACKTEST_VERSION = "qlib_official_daily_v3"
DEFAULT_PROVIDER_URI = Path("data/qlib_bin_daily_hfq")
DEFAULT_MARKET = "ashare_mainboard_no_st"
DEFAULT_BENCHMARK = "SH000300"
POOL_BENCHMARK = "ashare_mainboard_no_st_equal_weight_open"

DEFAULT_TOPK = 50
DEFAULT_N_DROP = 5
DEFAULT_RISK_DEGREE = 0.95
OPEN_COST = 0.0005
CLOSE_COST = 0.0015
MIN_COST = 5.0
IMPACT_COST = 0.0005
ACCOUNT_SIZE = 1_000_000.0
TRADE_UNIT = 100
LIMIT_THRESHOLD = 0.099
VOLUME_LIMIT_RATIO = 0.05
HARD_REJECT_TURNOVER_RATIO = 1.60
HARD_REJECT_DRAWDOWN_RATIO = 1.35
MIN_POSITIVE_RANKIC_FOLDS = 4

RESULTS_TSV_PATH = Path("results.tsv")
RUN_JSON_PATH = Path("run.json")
RUN_STATE_PATH = Path("run_state.json")
STATE_HELPER_PATH = Path("scripts/codex_supervisor_state.py")

LEGACY_RESULTS_HEADER = [
    "commit",
    "sharpe",
    "rank_ic",
    "turnover",
    "max_drawdown",
    "status",
    "description",
]
RESULTS_HEADER = [
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

TRADE_RETURN_EXPR = "Ref($open, -2) / Ref($open, -1) - 1"

_QLIB_INITIALIZED_URI: str | None = None


class SearchGovernanceError(RuntimeError):
    """Raised when the candidate violates the repository search policy."""


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
    external_sharpe: float
    raw_sharpe: float
    rank_ic: float
    turnover: float
    max_drawdown: float
    annual_return: float
    excess_annual_return: float
    benchmark_annual_return: float
    pool_benchmark_annual_return: float
    cost_rate: float
    num_days: int


@dataclass
class RunSummary:
    commit: str
    description: str
    status: str
    backtest_version: str
    mean_sharpe: float
    mean_external_sharpe: float
    mean_raw_sharpe: float
    mean_rank_ic: float
    mean_turnover: float
    mean_max_drawdown: float
    mean_annual_return: float
    mean_excess_annual_return: float
    mean_benchmark_annual_return: float
    mean_pool_benchmark_annual_return: float
    mean_cost_rate: float
    positive_rank_ic_folds: int
    positive_sharpe_folds: int
    worst_fold_sharpe: float
    fold_sharpe_std: float
    runtime_seconds: float
    provider_uri: str
    market: str
    benchmark: str
    pool_benchmark: str
    feature_fingerprint: str
    label_fingerprint: str
    experiment_fingerprint: str
    harness_status: str | None = None
    harness_decision_reason: str | None = None
    hard_reject: bool | None = None
    hard_reject_reason: str | None = None
    decision_reason: str | None = None
    baseline_commit: str | None = None
    baseline_description: str | None = None
    baseline_sharpe: float | None = None
    baseline_external_sharpe: float | None = None
    baseline_raw_sharpe: float | None = None
    baseline_rank_ic: float | None = None
    baseline_turnover: float | None = None
    baseline_max_drawdown: float | None = None
    sharpe_delta: float | None = None
    external_sharpe_delta: float | None = None
    raw_sharpe_delta: float | None = None
    rank_ic_delta: float | None = None
    turnover_ratio: float | None = None
    max_drawdown_ratio: float | None = None
    llm_decision: str | None = None
    llm_decision_reason: str | None = None
    llm_category: str | None = None
    final_status: str | None = None
    final_reason: str | None = None
    finalized: bool = False
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
        raise ValueError("Only model_type='lgbm' is supported in v3.")
    if not spec.feature_expressions:
        raise ValueError("At least one feature expression is required.")

    aliases = [alias for _, alias in spec.feature_expressions]
    if len(set(aliases)) != len(aliases):
        raise ValueError("Feature aliases must be unique.")
    if any(alias == "label" for alias in aliases):
        raise ValueError("Feature aliases cannot reuse reserved name: label.")

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
    fields.append(spec.label_expression)
    aliases.append("label")
    fields.append(TRADE_RETURN_EXPR)
    aliases.append("trade_return")

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


def make_signal_series(scored: pd.DataFrame) -> pd.Series:
    signal = scored["score"].swaplevel().sort_index()
    signal.index = signal.index.set_names(["datetime", "instrument"])
    return signal


def rank_ic_series(scored: pd.DataFrame) -> pd.Series:
    return scored.groupby(level="datetime").apply(compute_daily_rank_ic)


def pool_benchmark_returns(test_frame: pd.DataFrame) -> pd.Series:
    if "trade_return" not in test_frame.columns:
        return pd.Series(dtype=float)
    daily = test_frame.groupby(level="datetime")["trade_return"].mean()
    return daily.astype(float).sort_index()


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
    model.fit(
        X=train_frame[feature_names],
        y=train_frame["label"],
        eval_set=[(valid_frame[feature_names], valid_frame["label"])],
        eval_metric="l2",
        callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(period=0)],
    )
    ensure_time_budget(start_time)

    model_iteration = getattr(model, "best_iteration_", None) or getattr(model, "n_estimators_", None)
    predictions = model.predict(test_frame[feature_names], num_iteration=model_iteration)
    scored = test_frame.copy()
    scored["score"] = predictions

    qlib_signal = make_signal_series(scored)
    strategy = TopkDropoutStrategy(
        signal=qlib_signal,
        topk=int(spec.strategy_kwargs.get("topk", DEFAULT_TOPK)),
        n_drop=int(spec.strategy_kwargs.get("n_drop", DEFAULT_N_DROP)),
        risk_degree=float(spec.strategy_kwargs.get("risk_degree", DEFAULT_RISK_DEGREE)),
        only_tradable=True,
        forbid_all_trade_at_limit=True,
    )
    report_normal, _ = backtest_daily(
        start_time=fold.test_start,
        end_time=fold.test_end,
        strategy=strategy,
        account=ACCOUNT_SIZE,
        benchmark=DEFAULT_BENCHMARK,
        exchange_kwargs={
            "freq": "day",
            "codes": DEFAULT_MARKET,
            "deal_price": "open",
            "open_cost": OPEN_COST,
            "close_cost": CLOSE_COST,
            "min_cost": MIN_COST,
            "impact_cost": IMPACT_COST,
            "trade_unit": TRADE_UNIT,
            "limit_threshold": LIMIT_THRESHOLD,
            "volume_threshold": {"all": ("current", f"{VOLUME_LIMIT_RATIO} * $volume")},
        },
    )
    ensure_time_budget(start_time)

    if report_normal.empty:
        raise RuntimeError(f"{fold.name} produced empty portfolio metrics.")

    raw_returns = report_normal["return"].astype(float)
    benchmark_returns = report_normal["bench"].astype(float)
    cost_rates = report_normal["cost"].astype(float)
    external_excess_returns = raw_returns - benchmark_returns - cost_rates
    pool_returns = pool_benchmark_returns(test_frame).reindex(report_normal.index).fillna(0.0)
    excess_returns = raw_returns - pool_returns - cost_rates

    rank_ic = rank_ic_series(scored)
    return FoldMetrics(
        fold=fold.name,
        sharpe=compute_sharpe(excess_returns),
        external_sharpe=compute_sharpe(external_excess_returns),
        raw_sharpe=compute_sharpe(raw_returns),
        rank_ic=float(rank_ic.mean(skipna=True) or 0.0),
        turnover=float(report_normal["turnover"].mean() or 0.0),
        max_drawdown=compute_max_drawdown(raw_returns),
        annual_return=compute_annual_return(raw_returns),
        excess_annual_return=compute_annual_return(excess_returns),
        benchmark_annual_return=compute_annual_return(benchmark_returns),
        pool_benchmark_annual_return=compute_annual_return(pool_returns),
        cost_rate=float(cost_rates.mean() or 0.0),
        num_days=int(len(report_normal)),
    )


def compute_daily_rank_ic(day_frame: pd.DataFrame) -> float:
    usable = day_frame.dropna(subset=["score", "label"])
    if len(usable) < 2:
        return float("nan")
    if usable["score"].nunique() < 2 or usable["label"].nunique() < 2:
        return float("nan")
    return float(usable["score"].corr(usable["label"], method="spearman"))


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


def fingerprint_payload(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(encoded.encode("utf-8")).hexdigest()[:16]


def feature_fingerprint(spec: ExperimentSpec) -> str:
    return fingerprint_payload(spec.feature_expressions)


def label_fingerprint(spec: ExperimentSpec) -> str:
    return fingerprint_payload(spec.label_expression)


def experiment_fingerprint(spec: ExperimentSpec) -> str:
    return fingerprint_payload(
        {
            "features": spec.feature_expressions,
            "label": spec.label_expression,
            "model_type": spec.model_type,
            "model_kwargs": spec.model_kwargs,
            "strategy_kwargs": spec.strategy_kwargs,
            "seed": spec.seed,
            "backtest_version": BACKTEST_VERSION,
        }
    )


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
        backtest_version=BACKTEST_VERSION,
        mean_sharpe=recency_weighted_mean([fold.sharpe for fold in folds], folds),
        mean_external_sharpe=recency_weighted_mean([fold.external_sharpe for fold in folds], folds),
        mean_raw_sharpe=recency_weighted_mean([fold.raw_sharpe for fold in folds], folds),
        mean_rank_ic=recency_weighted_mean([fold.rank_ic for fold in folds], folds),
        mean_turnover=recency_weighted_mean([fold.turnover for fold in folds], folds),
        mean_max_drawdown=recency_weighted_mean([fold.max_drawdown for fold in folds], folds),
        mean_annual_return=recency_weighted_mean([fold.annual_return for fold in folds], folds),
        mean_excess_annual_return=recency_weighted_mean([fold.excess_annual_return for fold in folds], folds),
        mean_benchmark_annual_return=recency_weighted_mean([fold.benchmark_annual_return for fold in folds], folds),
        mean_pool_benchmark_annual_return=recency_weighted_mean(
            [fold.pool_benchmark_annual_return for fold in folds], folds
        ),
        mean_cost_rate=recency_weighted_mean([fold.cost_rate for fold in folds], folds),
        positive_rank_ic_folds=int(sum(fold.rank_ic > 0 for fold in folds)),
        positive_sharpe_folds=int(sum(fold.sharpe > 0 for fold in folds)),
        worst_fold_sharpe=float(min((fold.sharpe for fold in folds), default=0.0)),
        fold_sharpe_std=float(np.std([fold.sharpe for fold in folds], ddof=0)) if folds else 0.0,
        runtime_seconds=runtime_seconds,
        provider_uri=str(get_provider_uri()),
        market=DEFAULT_MARKET,
        benchmark=DEFAULT_BENCHMARK,
        pool_benchmark=POOL_BENCHMARK,
        feature_fingerprint=feature_fingerprint(spec),
        label_fingerprint=label_fingerprint(spec),
        experiment_fingerprint=experiment_fingerprint(spec),
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
    ensure_results_schema()
    if not RESULTS_TSV_PATH.exists() or RESULTS_TSV_PATH.stat().st_size == 0:
        return None

    results = pd.read_csv(RESULTS_TSV_PATH, sep="\t")
    if "backtest_version" in results.columns:
        results = results[results["backtest_version"] == BACKTEST_VERSION]
    kept = results[results["status"] == "keep"]
    if kept.empty:
        return None

    latest = kept.iloc[-1]
    return {
        "commit": str(latest["commit"]),
        "description": str(latest["description"]),
        "sharpe": float(latest["sharpe"]),
        "external_sharpe": (
            float(latest["external_sharpe"])
            if "external_sharpe" in latest and not pd.isna(latest.get("external_sharpe"))
            else float(latest["sharpe"])
        ),
        "raw_sharpe": float(latest["raw_sharpe"]) if not pd.isna(latest.get("raw_sharpe")) else float(latest["sharpe"]),
        "rank_ic": float(latest["rank_ic"]),
        "turnover": float(latest["turnover"]),
        "max_drawdown": float(latest["max_drawdown"]),
    }


def compute_ratio(current: float, baseline: float) -> float | None:
    if baseline <= 0:
        return None
    return current / baseline


def category_from_description(description: str) -> str:
    description_lower = description.lower()
    for category in ("factor", "label", "model", "strategy", "baseline"):
        if f"[{category}]" in description_lower:
            return category
    return "other"


def row_category(row: dict[str, Any]) -> str:
    category = str(row.get("category") or "").strip().lower()
    return category or category_from_description(str(row.get("description", "")))


def version_rows(results_path: Path) -> list[dict[str, str]]:
    ensure_results_schema()
    rows = load_results_tsv(results_path)
    return [row for row in rows if row.get("backtest_version") == BACKTEST_VERSION]


def load_results_tsv(results_path: Path) -> list[dict[str, str]]:
    if not results_path.exists():
        return []
    with results_path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def validate_search_governance(spec: ExperimentSpec) -> None:
    rows = [row for row in version_rows(RESULTS_TSV_PATH) if row.get("status") in {"keep", "discard"}]
    if not rows:
        return

    current_fingerprint = experiment_fingerprint(spec)
    current_category = category_from_description(spec.description)
    if current_category not in {"model", "strategy"}:
        return

    latest_finalized_category = row_category(rows[-1])
    if latest_finalized_category == current_category:
        if rows[-1].get("experiment_fingerprint") != current_fingerprint:
            raise SearchGovernanceError(
                f"Back-to-back {current_category}-only experiments are forbidden in {BACKTEST_VERSION}."
            )

    keeps = [row for row in rows if row.get("status") == "keep"]
    latest_keep_category = row_category(keeps[-1]) if keeps else None
    latest_keep_fingerprint = keeps[-1].get("experiment_fingerprint") if keeps else None

    if latest_keep_fingerprint == current_fingerprint:
        return

    if current_category == "strategy":
        strategy_count = sum(row_category(row) == "strategy" for row in rows)
        if len(rows) < 30 and strategy_count >= 2:
            raise SearchGovernanceError(
                f"Strategy-only experiments are capped at 2 during the first 30 {BACKTEST_VERSION} experiments."
            )
        if latest_keep_category not in {"factor", "label"}:
            raise SearchGovernanceError(
                "Strategy-only experiments require the latest keep in this backtest version to come from a factor or label idea."
            )

    if current_category == "model" and latest_keep_category not in {"factor", "label"}:
        raise SearchGovernanceError(
            "Model-only experiments require the latest keep in this backtest version to come from a factor or label idea."
        )


def evaluate_harness_status(summary: RunSummary) -> str:
    baseline = load_current_baseline()
    if baseline is None:
        summary.harness_decision_reason = "no_baseline_for_backtest_version"
        summary.decision_reason = "no_baseline_for_backtest_version"
        summary.hard_reject = False
        return "candidate"

    summary.baseline_commit = str(baseline["commit"])
    summary.baseline_description = str(baseline["description"])
    summary.baseline_sharpe = float(baseline["sharpe"])
    summary.baseline_external_sharpe = float(baseline["external_sharpe"])
    summary.baseline_raw_sharpe = float(baseline["raw_sharpe"])
    summary.baseline_rank_ic = float(baseline["rank_ic"])
    summary.baseline_turnover = float(baseline["turnover"])
    summary.baseline_max_drawdown = float(baseline["max_drawdown"])
    summary.sharpe_delta = summary.mean_sharpe - summary.baseline_sharpe
    summary.external_sharpe_delta = summary.mean_external_sharpe - summary.baseline_external_sharpe
    summary.raw_sharpe_delta = summary.mean_raw_sharpe - summary.baseline_raw_sharpe
    summary.rank_ic_delta = summary.mean_rank_ic - summary.baseline_rank_ic
    summary.turnover_ratio = compute_ratio(summary.mean_turnover, summary.baseline_turnover)
    summary.max_drawdown_ratio = compute_ratio(summary.mean_max_drawdown, summary.baseline_max_drawdown)

    if summary.mean_rank_ic <= 0.0:
        summary.hard_reject = True
        summary.hard_reject_reason = "rankic_nonpositive"
        summary.harness_decision_reason = "hard_reject"
        summary.decision_reason = "hard_reject"
        return "hard_reject"
    if summary.positive_rank_ic_folds < MIN_POSITIVE_RANKIC_FOLDS:
        summary.hard_reject = True
        summary.hard_reject_reason = "rankic_fold_instability"
        summary.harness_decision_reason = "hard_reject"
        summary.decision_reason = "hard_reject"
        return "hard_reject"
    if summary.turnover_ratio is not None and summary.turnover_ratio > HARD_REJECT_TURNOVER_RATIO:
        summary.hard_reject = True
        summary.hard_reject_reason = "turnover_extreme"
        summary.harness_decision_reason = "hard_reject"
        summary.decision_reason = "hard_reject"
        return "hard_reject"
    if summary.max_drawdown_ratio is not None and summary.max_drawdown_ratio > HARD_REJECT_DRAWDOWN_RATIO:
        summary.hard_reject = True
        summary.hard_reject_reason = "drawdown_extreme"
        summary.harness_decision_reason = "hard_reject"
        summary.decision_reason = "hard_reject"
        return "hard_reject"

    summary.hard_reject = False
    summary.harness_decision_reason = "llm_decision_required"
    summary.decision_reason = "llm_decision_required"
    return "candidate"


def ensure_results_schema() -> None:
    if not RESULTS_TSV_PATH.exists():
        RESULTS_TSV_PATH.write_text("\t".join(RESULTS_HEADER) + "\n", encoding="utf-8")
        return

    with RESULTS_TSV_PATH.open(encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fieldnames = reader.fieldnames or []
        if fieldnames == RESULTS_HEADER:
            return
        rows = list(reader)

    migrated_rows: list[dict[str, str]] = []
    for row in rows:
        migrated_rows.append(
            {
                "commit": row.get("commit", ""),
                "backtest_version": row.get("backtest_version", "v1_legacy"),
                "sharpe": row.get("sharpe", ""),
                "external_sharpe": row.get("external_sharpe", row.get("sharpe", "")),
                "raw_sharpe": row.get("raw_sharpe", row.get("sharpe", "")),
                "rank_ic": row.get("rank_ic", ""),
                "turnover": row.get("turnover", ""),
                "max_drawdown": row.get("max_drawdown", ""),
                "status": row.get("status", ""),
                "category": row.get("category", ""),
                "baseline_commit": row.get("baseline_commit", ""),
                "experiment_fingerprint": row.get("experiment_fingerprint", ""),
                "description": row.get("description", ""),
            }
        )

    with RESULTS_TSV_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULTS_HEADER, delimiter="\t")
        writer.writeheader()
        writer.writerows(migrated_rows)


def append_results_tsv(summary: RunSummary) -> None:
    ensure_results_schema()
    row = {
        "commit": summary.commit,
        "backtest_version": summary.backtest_version,
        "sharpe": f"{summary.mean_sharpe:.6f}",
        "external_sharpe": f"{summary.mean_external_sharpe:.6f}",
        "raw_sharpe": f"{summary.mean_raw_sharpe:.6f}",
        "rank_ic": f"{summary.mean_rank_ic:.6f}",
        "turnover": f"{summary.mean_turnover:.6f}",
        "max_drawdown": f"{summary.mean_max_drawdown:.6f}",
        "status": summary.status,
        "category": summary.llm_category or "",
        "baseline_commit": summary.baseline_commit or "",
        "experiment_fingerprint": summary.experiment_fingerprint,
        "description": sanitize_tsv_field(summary.description),
    }
    with RESULTS_TSV_PATH.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULTS_HEADER, delimiter="\t")
        writer.writerow(row)


def sanitize_tsv_field(value: str) -> str:
    return value.replace("\t", " ").replace("\n", " ").strip()


def load_run_state() -> dict[str, Any]:
    if not RUN_STATE_PATH.exists():
        return {
            "version": 1,
            "phase": "idle",
            "backtest_version": BACKTEST_VERSION,
            "latest_keep_commit": None,
            "latest_finalized_commit": None,
            "latest_finalized_status": None,
            "current_candidate_commit": None,
            "current_head_commit": current_commit_hash(),
        }
    return json.loads(RUN_STATE_PATH.read_text(encoding="utf-8"))


def save_run_state(state: dict[str, Any]) -> None:
    RUN_STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sync_branch_projection() -> None:
    if not STATE_HELPER_PATH.exists():
        return
    try:
        subprocess.run(
            [sys.executable, str(STATE_HELPER_PATH), "sync-branch-state", "--repo-root", "."],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        print(f"branch_sync: warning ({exc})", file=sys.stderr)


def update_run_state_after_result(summary: RunSummary) -> None:
    state = load_run_state()
    state["version"] = 1
    state["backtest_version"] = summary.backtest_version
    state["current_head_commit"] = summary.commit
    state["latest_description"] = summary.description
    if summary.status in {"candidate", "hard_reject"}:
        state["phase"] = "candidate_recorded"
        state["current_candidate_commit"] = summary.commit
    else:
        state["phase"] = "finalized_crash"
        state["current_candidate_commit"] = None
        state["latest_finalized_commit"] = summary.commit
        state["latest_finalized_status"] = summary.status
    save_run_state(state)


def write_run_json(summary: RunSummary) -> None:
    RUN_JSON_PATH.write_text(json.dumps(asdict(summary), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def print_summary(summary: RunSummary) -> None:
    print("---")
    print(f"status:                 {summary.status}")
    print(f"backtest_version:       {summary.backtest_version}")
    if summary.decision_reason:
        print(f"decision_reason:        {summary.decision_reason}")
    if summary.harness_decision_reason:
        print(f"harness_reason:         {summary.harness_decision_reason}")
    if summary.hard_reject is not None:
        print(f"hard_reject:            {str(summary.hard_reject).lower()}")
    if summary.hard_reject_reason:
        print(f"hard_reject_reason:     {summary.hard_reject_reason}")
    print(f"mean_sharpe:            {summary.mean_sharpe:.6f}")
    print(f"mean_external_sharpe:   {summary.mean_external_sharpe:.6f}")
    print(f"mean_raw_sharpe:        {summary.mean_raw_sharpe:.6f}")
    print(f"mean_rank_ic:           {summary.mean_rank_ic:.6f}")
    print(f"mean_turnover:          {summary.mean_turnover:.6f}")
    print(f"mean_max_drawdown:      {summary.mean_max_drawdown:.6f}")
    print(f"mean_annual_return:     {summary.mean_annual_return:.6f}")
    print(f"mean_excess_annual_ret: {summary.mean_excess_annual_return:.6f}")
    print(f"mean_benchmark_return:  {summary.mean_benchmark_annual_return:.6f}")
    print(f"mean_pool_bench_return: {summary.mean_pool_benchmark_annual_return:.6f}")
    print(f"mean_cost_rate:         {summary.mean_cost_rate:.6f}")
    print(f"positive_rank_ic_folds: {summary.positive_rank_ic_folds}")
    print(f"worst_fold_sharpe:      {summary.worst_fold_sharpe:.6f}")
    print(f"fold_sharpe_std:        {summary.fold_sharpe_std:.6f}")
    print(f"runtime_seconds:        {summary.runtime_seconds:.1f}")
    print(f"description:            {summary.description}")
    if summary.error:
        print(f"error:                  {summary.error}")


def run_experiment(spec: ExperimentSpec) -> RunSummary:
    validate_experiment_spec(spec)
    start_time = time.perf_counter()
    folds: list[FoldMetrics] = []

    try:
        validate_search_governance(spec)
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
    except SearchGovernanceError as exc:
        runtime_seconds = time.perf_counter() - start_time
        summary = aggregate_summary(
            spec=spec,
            folds=folds,
            runtime_seconds=runtime_seconds,
            status="hard_reject",
            error=f"{exc.__class__.__name__}: {exc}",
        )
        summary.hard_reject = True
        summary.hard_reject_reason = "search_governance_violation"
        summary.harness_decision_reason = "hard_reject"
        summary.decision_reason = "hard_reject"
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
        summary.harness_decision_reason = "crash"
        summary.decision_reason = "crash"

    write_run_json(summary)
    update_run_state_after_result(summary)
    append_results_tsv(summary)
    sync_branch_projection()
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
