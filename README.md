# autoresearch

This repository is an `autoresearch`-style quant research fork built on Qlib and A-share daily data.

It is intentionally narrow:

- daily A-share mainboard research
- price/volume/turnover-driven trading alpha
- one mutable experiment file: `train.py`
- one fixed harness: `prepare.py`
- one autonomous operating manual: `program.md`

It is **not** a production execution stack and **not** a full fundamental multi-factor platform.

## What Changed In v3

The repository now uses a stricter `qlib_official_daily_v3` evaluation contract:

- the backtest path is based on Qlib official daily backtest semantics
- the decision metric is pool-benchmark excess-with-cost Sharpe
- external HS300 excess Sharpe is still reported separately
- exchange-level volume participation and impact cost constraints are enabled
- results are versioned by `backtest_version`
- `run_state.json` separates workflow state from experiment summary
- `results.tsv` carries versioned compact ledger rows

Because of that change, old `v1_legacy` and `qlib_official_daily_v2` history stays useful for archaeology, but new `v3` experiments must compare only against `v3` baselines.

## How It Works

Three files define the workflow:

- `prepare.py` — fixed Qlib harness, backtest, summary serialization, and hard rejects
- `train.py` — the only file the agent edits between experiments
- `program.md` — the operating policy for the agent

The philosophy is the same as the original repo:

- keep the codebase small
- mutate one experiment file at a time
- run one short comparable experiment
- advance only when the candidate is actually better

## Quick Start

```bash
cd /Users/zhaozhiyu/Projects/autoresearch
mkdir -p tmp/mplconfig

MPLCONFIGDIR=$PWD/tmp/mplconfig \
QLIB_PROVIDER_URI=${QLIB_PROVIDER_URI:-$PWD/data/qlib_bin_daily_hfq} \
conda run -n qlib python prepare.py --check
```

Then run the current experiment definition:

```bash
MPLCONFIGDIR=$PWD/tmp/mplconfig \
QLIB_PROVIDER_URI=${QLIB_PROVIDER_URI:-$PWD/data/qlib_bin_daily_hfq} \
conda run -n qlib python train.py > run.log 2>&1
```

After each run inspect:

- `run_state.json`
- `run.json`
- `results.tsv`

## Runtime Notes

- The repo does **not** use `uv` for experiments.
- Use the existing `qlib` conda environment.
- `prepare.py --check` is the only supported provider/runtime verification step.
- `run.log` is a debug artifact, not the primary decision source.

## Output Artifacts

Each run produces:

- `run.json` — latest experiment summary
- `run_state.json` — workflow state and keep/finalized/candidate semantics
- `results.tsv` — compact versioned ledger
- `run.log` — raw stdout/stderr if you redirected output

The repo root copies above are the **current branch projection**.
Branch-scoped source-of-truth archives live under `tmp/codex_supervisor/<branch_slug>/`.

The v3 summary includes:

- `mean_sharpe` (decision metric: pool-benchmark excess-with-cost Sharpe)
- `mean_external_sharpe`
- `mean_raw_sharpe`
- `mean_rank_ic`
- `mean_turnover`
- `mean_max_drawdown`
- `mean_annual_return`
- `mean_excess_annual_return`
- `mean_benchmark_annual_return`
- `mean_pool_benchmark_annual_return`
- `mean_cost_rate`
- fold-stability diagnostics

## Running The Agent

For Codex, the recommended path remains the external supervisor launcher:

```bash
./scripts/run_codex_autoresearch.sh --model gpt-5.4
```

The supervisor still exists for the same reason as before: interactive “never stop” behavior is not reliable enough on its own.

Important v3 operating rules:

- inspect `run_state.json` before every step
- compare only within the same `backtest_version`
- prefer `run.json` over `run.log`
- treat strategy-only changes as rare follow-up checks, not the main search path

## Research Policy

The search priority in v3 is:

1. factor family experiments
2. label family experiments
3. rare model follow-up checks
4. rare strategy follow-up checks

Promising family lanes include:

- overnight gap / overnight-intraday decomposition
- low-risk / liquidity
- behavioral / trading activity
- trend alignment
- label decomposition

## Data Contract

- Default provider path: `data/qlib_bin_daily_hfq`
- Market: `ashare_mainboard_no_st`
- Frequency: daily
- Fields expected by the workflow:
  - `$open`
  - `$high`
  - `$low`
  - `$close`
  - `$volume`
  - `$factor`
  - `$turnover_rate`
- `vwap` is intentionally unsupported in this repository version

## Project Structure

```text
prepare.py      — fixed Qlib harness, read-only during the loop
train.py        — single editable experiment file
program.md      — autonomous-agent operating manual
README.md       — repo overview
```

## Operating Principle

This repository is for fast, comparable, autonomous daily-equity signal research.
It is not a generic trading platform and not a production execution engine.
