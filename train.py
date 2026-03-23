"""
Autoresearch-style quant experiment file.

This is the only file the autonomous loop edits between experiments.
The evaluation harness lives in prepare.py and is treated as read-only.
"""

from __future__ import annotations

from prepare import ExperimentSpec, run_experiment


def build_experiment() -> ExperimentSpec:
    return ExperimentSpec(
        description="[label][docs] open3d_capped12",
        feature_expressions=[
            ("$close / $open - 1", "intraday_return"),
            ("$open / Ref($close, 1) - 1", "gap_return"),
            ("$close / Ref($close, 1) - 1", "close_return_1"),
            ("$close / Ref($close, 5) - 1", "close_return_5"),
            ("Mean($close, 5) / $close - 1", "ma_gap_5"),
            ("Mean($close, 20) / $close - 1", "ma_gap_20"),
            ("Std($close, 5) / $close", "close_vol_5"),
            ("Std($close, 20) / $close", "close_vol_20"),
            ("($high - $low) / $close", "range_pct"),
            ("Max($high, 5) / $close - 1", "high_breakout_5"),
            ("$close / Min($low, 5) - 1", "low_rebound_5"),
            ("Mean($volume, 5) / Mean($volume, 20) - 1", "volume_ratio_5_20"),
            ("Std($volume, 20) / Mean($volume, 20)", "volume_vol_20"),
            ("$turnover_rate", "turnover_rate"),
            ("Mean($turnover_rate, 5)", "turnover_rate_mean_5"),
            ("Std($turnover_rate, 20)", "turnover_rate_vol_20"),
            ("($close-$open)/($high-$low+1e-12)", "alpha_kmid2"),
            ("($high-Greater($open, $close))/($high-$low+1e-12)", "alpha_kup2"),
            ("(Less($open, $close)-$low)/($high-$low+1e-12)", "alpha_klow2"),
            ("(2*$close-$high-$low)/($high-$low+1e-12)", "alpha_ksft2"),
            ("($close-Min($low, 20))/(Max($high, 20)-Min($low, 20)+1e-12)", "alpha_rsv20"),
            ("Corr($close, Log($volume+1), 20)", "alpha_corr20"),
            ("Corr($close/Ref($close,1), Log($volume/Ref($volume, 1)+1), 20)", "alpha_cord20"),
            (
                "Mean($close>Ref($close, 1), 20)-Mean($close<Ref($close, 1), 20)",
                "alpha_cntd20",
            ),
            (
                "(Sum(Greater($close-Ref($close, 1), 0), 20)-Sum(Greater(Ref($close, 1)-$close, 0), 20))"
                "/(Sum(Abs($close-Ref($close, 1)), 20)+1e-12)",
                "alpha_sumd20",
            ),
            (
                "Std(Abs($close/Ref($close, 1)-1)*$volume, 20)"
                "/(Mean(Abs($close/Ref($close, 1)-1)*$volume, 20)+1e-12)",
                "alpha_wvma20",
            ),
            (
                "(Sum(Greater($volume-Ref($volume, 1), 0), 20)-Sum(Greater(Ref($volume, 1)-$volume, 0), 20))"
                "/(Sum(Abs($volume-Ref($volume, 1)), 20)+1e-12)",
                "alpha_vsumd20",
            ),
        ],
        label_expression=(
            "Greater(Less(Ref($open, -4) / Ref($open, -1) - 1, 0.12), -0.12)"
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
        strategy_kwargs={"topk": 40, "n_drop": 4},
        seed=42,
    )


if __name__ == "__main__":
    run_experiment(build_experiment())
