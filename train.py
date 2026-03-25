"""
Autoresearch-style quant experiment file.

This is the only file the autonomous loop edits between experiments.
The evaluation harness lives in prepare.py and is treated as read-only.
"""

from __future__ import annotations

from prepare import ExperimentSpec, run_experiment


def build_experiment() -> ExperimentSpec:
    return ExperimentSpec(
        description="[strategy][local] overnight_gap_top44_drop3",
        feature_expressions=[
            ("($close - $open) / $open", "kmid"),
            ("($high - $low) / $open", "klen"),
            ("($high - Greater($open, $close)) / ($high - $low + 1e-12)", "kup2"),
            ("(Less($open, $close) - $low) / ($high - $low + 1e-12)", "klow2"),
            ("(2 * $close - $high - $low) / ($high - $low + 1e-12)", "ksft2"),
            ("$open / Ref($close, 1) - 1", "gap1"),
            ("Mean($open / Ref($close, 1) - 1, 5)", "gap_mean5"),
            ("Std($open / Ref($close, 1) - 1, 20)", "gap_std20"),
            ("Rank($open / Ref($close, 1) - 1, 20)", "gap_rank20"),
            ("Mean((($close - $open) / $open) - ($open / Ref($close, 1) - 1), 5)", "gap_reversal5"),
            (
                "($open / Ref($close, 1) - 1) / (Mean(Abs($open / Ref($close, 1) - 1), 20) + 1e-12)",
                "gap_shock20",
            ),
            (
                "Sum($open / Ref($close, 1) - 1, 20) / (Sum(Abs($open / Ref($close, 1) - 1), 20) + 1e-12)",
                "gap_sumd20",
            ),
            (
                "Mean($open / Ref($close, 1) - 1 > 0, 20) - Mean($open / Ref($close, 1) - 1 < 0, 20)",
                "gap_streak20",
            ),
            (
                "Mean(($open / Ref($close, 1) - 1) - Ref($open / Ref($close, 1) - 1, 1), 5)",
                "gap_accel5",
            ),
            (
                "Corr($open / Ref($close, 1) - 1, Ref($open / Ref($close, 1) - 1, 1), 20)",
                "gap_autocorr20",
            ),
            ("Mean($close, 20) / $close", "ma20"),
            ("Std($close, 20) / $close", "std20"),
            ("Slope($close, 20) / $close", "beta20"),
            ("Rsquare($close, 20)", "rsqr20"),
            ("Resi($close, 20) / $close", "resi20"),
            ("Rank($close, 20)", "rank20"),
            ("($close - Min($low, 20)) / (Max($high, 20) - Min($low, 20) + 1e-12)", "rsv20"),
            ("IdxMax($high, 20) / 20", "imax20"),
            ("IdxMin($low, 20) / 20", "imin20"),
            ("(IdxMax($high, 20) - IdxMin($low, 20)) / 20", "imxd20"),
            ("(Sum(Greater($close - Ref($close, 1), 0), 20) - Sum(Greater(Ref($close, 1) - $close, 0), 20)) / (Sum(Abs($close - Ref($close, 1)), 20) + 1e-12)", "sumd20"),
            ("Mean($close > Ref($close, 1), 20) - Mean($close < Ref($close, 1), 20)", "cntd20"),
            ("Corr($close, Log($volume + 1), 20)", "corr20"),
            ("Corr($close / Ref($close, 1), Log($volume / Ref($volume, 1) + 1), 20)", "cord20"),
            ("Std(Abs($close / Ref($close, 1) - 1) * $volume, 20) / (Mean(Abs($close / Ref($close, 1) - 1) * $volume, 20) + 1e-12)", "wvma20"),
            ("$turnover_rate", "turnover_rate"),
            ("Mean($turnover_rate, 5) / (Mean($turnover_rate, 20) + 1e-12)", "turnover_ratio_5_20"),
            ("Std($turnover_rate, 20) / (Mean($turnover_rate, 20) + 1e-12)", "turnover_vol_20"),
        ],
        label_expression=(
            "0.5 * (Ref($open, -4) / Ref($open, -1) - 1) + "
            "0.5 * (Ref($open, -6) / Ref($open, -1) - 1)"
        ),
        model_type="lgbm",
        model_kwargs={
            "n_estimators": 300,
            "learning_rate": 0.05,
            "num_leaves": 64,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "min_child_samples": 100,
            "reg_lambda": 1.0,
        },
        strategy_kwargs={"topk": 44, "n_drop": 3},
        seed=42,
    )


if __name__ == "__main__":
    run_experiment(build_experiment())