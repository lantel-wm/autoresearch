# autoresearch

This repository is an experiment in autonomous A-share daily trading-alpha research.

It is **not** a general-purpose production trading stack and **not** a full cross-sectional
fundamental multi-factor platform. The current scope is narrower:

- daily A-share mainboard research
- price/volume/turnover-driven factor families
- realistic open-price execution alignment
- fixed Qlib v2 backtest semantics
- one mutable experiment file: `train.py`

## Setup

To set up a new run, work with the user to:

1. Agree on a run tag such as `mar27`.
2. Create `autoresearch/<tag>` from the current main branch.
3. Read `README.md`, `prepare.py`, `train.py`, and this file.
4. Verify the provider exists at `data/qlib_bin_daily_hfq` or set `QLIB_PROVIDER_URI`.
5. Verify runtime with:

   ```bash
   MPLCONFIGDIR=$PWD/tmp/mplconfig \
   QLIB_PROVIDER_URI=${QLIB_PROVIDER_URI:-$PWD/data/qlib_bin_daily_hfq} \
   conda run -n qlib python prepare.py --check
   ```

6. Inspect `run_state.json`, `run.json`, and `results.tsv` before the first experiment step.

## Experimentation Contract

- Modify `train.py` only.
- Keep `prepare.py` read-only during the loop.
- Run everything in the local `qlib` conda environment.
- The fixed market is `ashare_mainboard_no_st`.
- The fixed frequency is daily.
- Supported fields are `open`, `high`, `low`, `close`, `volume`, `factor`, `turnover_rate`.
- `vwap` is out of scope in this repository version.
- The backtest version is `qlib_official_daily_v2`.

## Research Positioning

Treat this repo as a **daily trading-alpha** workflow.

That means:

- prioritize overnight/intraday, low-risk, liquidity, behavioral, and trend-alignment families
- do not pretend this is a full valuation/quality/size factor platform unless the data contract expands
- evaluate ideas as trading signals first, not as broad economic factor claims

## Search Governance

The default lane order is:

1. factor family experiments
2. label family experiments
3. rare model follow-up checks
4. rare strategy follow-up checks

Hard rules:

- Strategy-only experiments are frozen by default.
- Do not run a strategy-only experiment unless the immediately preceding kept result came from a new factor or label idea.
- In the first 30 experiments of a new backtest version, strategy-only experiments may appear at most twice.
- Do not run back-to-back strategy-only or model-only experiments.
- Do not do local sweeps on `topk`, `n_drop`, `learning_rate`, `num_leaves`, or `n_estimators`.
- Every experiment must declare one family-level hypothesis, not a random formula grab-bag.

## Family Taxonomy

Use one of these family tags in the description and reasoning:

- `overnight_gap`
- `lowrisk_liquidity`
- `behavioral_trading`
- `trend_alignment`
- `label_decomposition`
- `model_followup`
- `strategy_followup`

Descriptions should stay short and TSV-safe, but should still reveal the family and source, for example:

- `[factor][paper] overnight_gap gaptrend_align55`
- `[label][local] label_decomposition open467_component_cap30_top7`

## Web Research Policy

- Prefer official Qlib docs, Microsoft Qlib / RD-Agent material, and papers.
- Treat blogs, forums, and practitioner posts as hypothesis generators only.
- If an experiment is externally inspired, keep compact tags like `[factor][paper]` or `[label][docs]`.
- Search for family-level evidence first, then map it into one concrete expression or label change.

## State Files

Three files matter and they have different meanings:

- `run_state.json`: current workflow state, including keep/finalized/candidate semantics
- `run.json`: latest experiment summary
- `results.tsv`: compact ledger across all historical experiments

Do not confuse them.

- `run_state.json` is the source of truth for whether the repo is idle, has a pending candidate, or has already finalized the latest run.
- `run.json` is the source of truth for the latest experiment metrics.
- `results.tsv` is the long-term ledger.

## Output Format

`prepare.py` prints a compact summary and writes:

- `run.json`
- `run_state.json`
- `results.tsv`

The v2 summary includes:

- `mean_sharpe` (decision metric: excess-with-cost Sharpe)
- `mean_raw_sharpe`
- `mean_rank_ic`
- `mean_turnover`
- `mean_max_drawdown`
- `mean_annual_return`
- `mean_excess_annual_return`
- `mean_benchmark_annual_return`
- `mean_cost_rate`
- stability diagnostics such as positive RankIC folds and worst-fold Sharpe

## Decision Rule

The harness owns:

- metrics
- backtest semantics
- backtest versioning
- hard reject rules

The LLM owns:

- final `keep` / `discard`
- experiment category
- short decision reason

The LLM should only judge candidates that survive the harness filters.

## Logging Results

`results.tsv` stays untracked by git and is now versioned by `backtest_version`.

Do not compare results across different `backtest_version` values.

If the backtest version changes, start from a fresh baseline under the new version.

## The Experiment Loop

Repeat this cycle:

1. Inspect `git` state, `run_state.json`, `results.tsv`, `run.json`, and `train.py`.
2. If web research is useful, do a short family-level research pass.
3. Modify `train.py` for exactly one hypothesis.
4. Commit the change.
5. Run:

   ```bash
   MPLCONFIGDIR=$PWD/tmp/mplconfig \
   QLIB_PROVIDER_URI=${QLIB_PROVIDER_URI:-$PWD/data/qlib_bin_daily_hfq} \
   conda run -n qlib python train.py > run.log 2>&1
   ```

6. Read `run.json` first. Do not use raw `run.log` as a primary decision input.
7. If `status` is `candidate`, compare it against the current kept baseline in the same `backtest_version`.
8. Finalize with:

   ```bash
   python3 scripts/codex_supervisor_state.py finalize-result \
     --repo-root . \
     --decision keep|discard \
     --category factor|label|model|strategy|baseline|other \
     --reason "short reason"
   ```

9. If the final decision is `keep`, keep the commit.
10. If the final decision is `discard`, revert to the previous kept `train.py` state.
11. Leave the repository in an idle state that is ready for the next iteration.

## Crash Handling

- Prefer the structured `error` field in `run.json`.
- Only inspect raw traceback from `run.log` if the structured error is insufficient.
- If the crash is trivial and the hypothesis still makes sense, fix and rerun once.
- Otherwise discard and move on.

## Simplicity Criterion

All else equal, prefer smaller diffs and more interpretable factor-family changes.

## Continuation

Once the loop begins, keep iterating until the human interrupts you or a real structural blocker appears.
