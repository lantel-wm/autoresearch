"""
Autoresearch-style quant experiment file.

This is the only file the autonomous loop edits between experiments.
The evaluation harness lives in prepare.py and is treated as read-only.
"""

from __future__ import annotations

from prepare import ExperimentSpec, run_experiment


def build_experiment() -> ExperimentSpec:
    return ExperimentSpec(
        description="[factor][paper] behavioral_trading negday_turnpressure5",
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
            ("$close / Ref($close, 5) - 1", "mom5"),
            ("$close / Ref($close, 10) - 1", "mom10"),
            (
                "($close / Ref($close, 5) - 1) * "
                "(Mean($turnover_rate, 5) / (Mean($turnover_rate, 20) + 1e-12))",
                "mom_turn5",
            ),
            (
                "($close / Ref($close, 10) - 1) * "
                "(Mean($turnover_rate, 5) / (Mean($turnover_rate, 20) + 1e-12))",
                "mom_turn10",
            ),
            (
                "Mean($close / $open - 1, 10) * "
                "(Mean($turnover_rate, 5) / (Mean($turnover_rate, 20) + 1e-12))",
                "intraday_mom_turn10",
            ),
            (
                "(($close / $open - 1) * ($close < $open)) * "
                "($turnover_rate / (Mean($turnover_rate, 20) + 1e-12))",
                "negday_turnshock1",
            ),
            (
                "(($close / $open - 1) * ($close > $open)) * "
                "($turnover_rate / (Mean($turnover_rate, 20) + 1e-12))",
                "posday_turnshock1",
            ),
            (
                "Mean(Abs($close / $open - 1) * ($close < $open) * "
                "($turnover_rate / (Mean($turnover_rate, 20) + 1e-12)), 5)",
                "negday_turnpressure5",
            ),
            (
                "($close / Ref($close, 10) - 1) * "
                "(Mean($turnover_rate, 20) / (Std($turnover_rate, 20) + 1e-12))",
                "mom_lowrisk10",
            ),
            (
                "($open / Ref($close, 1) - 1) / (Mean(Abs($open / Ref($close, 1) - 1), 20) + 1e-12)",
                "gap_shock20",
            ),
            (
                "Sum($open / Ref($close, 1) - 1, 20) / (Sum(Abs($open / Ref($close, 1) - 1), 20) + 1e-12)",
                "gap_sumd20",
            ),
            (
                "(Mean((($close - $open) / (Ref($close, 1) - $open + 1e-12)) * "
                "($open > Ref($high, 1)), 20) - "
                "Mean((($close - $open) / (Ref($close, 1) - $open + 1e-12)) * "
                "($open < Ref($low, 1)), 20)) + "
                "(Mean((($close - $open) / (Ref($close, 1) - $open + 1e-12)) * "
                "(($open > Ref($close, 1)) * ($open <= Ref($high, 1))), 20) - "
                "Mean((($close - $open) / (Ref($close, 1) - $open + 1e-12)) * "
                "(($open < Ref($close, 1)) * ($open >= Ref($low, 1))), 20))",
                "gap_fill_type_asym20",
            ),
            (
                "(($close - $open) / (Ref($close, 1) - $open + 1e-12)) * "
                "($volume / (Mean($volume, 20) + 1e-12))",
                "gap_fill_volume1",
            ),
            (
                "((($close - $open) / (Ref($close, 1) - $open + 1e-12))) * "
                "(Mean($turnover_rate, 5) / (Mean($turnover_rate, 20) + 1e-12))",
                "gap_fill_turnover1",
            ),
            (
                "Mean((($close - $open) / (Ref($close, 1) - $open + 1e-12)) * "
                "(Mean($volume, 20) / ($volume + 1e-12)), 20)",
                "gap_fill_low_volume20",
            ),
            ("Std($close / Ref($close, 1) - 1, 20)", "retvol20"),
            ("Std($close / Ref($close, 1) - 1, 55)", "retvol55"),
            ("Abs(Resi($close, 10) / $close)", "abs_resi10"),
            ("Abs(Resi($close, 20) / $close)", "abs_resi20"),
            (
                "(Sum($open / Ref($close, 1) - 1, 20) / (Sum(Abs($open / Ref($close, 1) - 1), 20) + 1e-12)) / "
                "(Std($close / Ref($close, 1) - 1, 20) + 1e-12)",
                "lowvol_gap20",
            ),
            (
                "Mean((($close - $open) / (Ref($close, 1) - $open + 1e-12)), 20) / "
                "(Std($close / Ref($close, 1) - 1, 20) + 1e-12)",
                "conservative_gapfill20",
            ),
            (
                "Mean($turnover_rate, 20) / (Std($turnover_rate, 20) + 1e-12)",
                "lowrisk_turn20",
            ),
            (
                "(Sum($open / Ref($close, 1) - 1, 20) / (Sum(Abs($open / Ref($close, 1) - 1), 20) + 1e-12)) / "
                "(Abs(Resi($close, 20) / $close) + 1e-12)",
                "idio_gap20",
            ),
            (
                "Mean((($close - $open) / (Ref($close, 1) - $open + 1e-12)), 20) / "
                "(Abs(Resi($close, 20) / $close) + 1e-12)",
                "idio_gapfill20",
            ),
            (
                "($close / Ref($close, 10) - 1) / "
                "(Abs(Resi($close, 20) / $close) + 1e-12)",
                "mom_idio10",
            ),
            (
                "($close / Ref($close, 10) - 1) * "
                "(Sum($open / Ref($close, 1) - 1, 20) / (Sum(Abs($open / Ref($close, 1) - 1), 20) + 1e-12))",
                "mom_gap10",
            ),
            ("Mean($close, 55) / $close", "ma55"),
            ("Std($close, 55) / $close", "std55"),
            ("Slope($close, 55) / $close", "beta55"),
            ("Rsquare($close, 55)", "rsqr55"),
            ("Resi($close, 55) / $close", "resi55"),
            ("Rank($close, 55)", "rank55"),
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
            "Greater(Less("
            "0.25 * (Ref($open, -4) / Ref($open, -1) - 1) + "
            "0.50 * (Ref($open, -6) / Ref($open, -1) - 1) + "
            "0.25 * (Ref($open, -7) / Ref($open, -1) - 1)"
            ", 0.30), -0.30)"
        ),
        model_type="lgbm",
        model_kwargs={
            "n_estimators": 700,
            "learning_rate": 0.02,
            "num_leaves": 64,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "min_child_samples": 100,
            "reg_lambda": 1.0,
        },
        strategy_kwargs={"topk": 7, "n_drop": 2},
        seed=42,
    )


if __name__ == "__main__":
    run_experiment(build_experiment())
