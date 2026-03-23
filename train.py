"""
Autoresearch-style quant experiment file.

This is the only file the autonomous loop edits between experiments.
The evaluation harness lives in prepare.py and is treated as read-only.
"""

from __future__ import annotations

from prepare import ExperimentSpec, run_experiment


def build_experiment() -> ExperimentSpec:
    return ExperimentSpec(
        description="[label][docs] open_to_open_5d_aligned",
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
        ],
        label_expression="Ref($open, -6) / Ref($open, -1) - 1",
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
        strategy_kwargs={"topk": 50, "n_drop": 5},
        seed=42,
    )


if __name__ == "__main__":
    run_experiment(build_experiment())
