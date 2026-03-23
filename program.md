# autoresearch

This repository is an experiment in autonomous quant research.

## Setup

To set up a new run, work with the user to:

1. **Agree on a run tag**: propose a tag based on today's date, e.g. `mar23`. The branch `autoresearch/<tag>` must not already exist.
2. **Create the branch**: `git checkout -b autoresearch/<tag>` from current main branch.
3. **Read the in-scope files**:
   - `README.md` for repository context.
   - `prepare.py` for the fixed Qlib harness. Do not modify this file during the loop.
   - `train.py` for the experiment definition. This is the only file you edit during experiments.
4. **Verify the provider exists**: the default provider path is `data/qlib_bin_daily_hfq`. If it does not exist, either tell the human to copy it into this worktree or set `QLIB_PROVIDER_URI` explicitly.
5. **Verify the runtime**: run `MPLCONFIGDIR=$PWD/tmp/mplconfig QLIB_PROVIDER_URI=${QLIB_PROVIDER_URI:-$PWD/data/qlib_bin_daily_hfq} conda run -n qlib python prepare.py --check`.
6. **Initialize results tracking**: `results.tsv` is created automatically on the first run. It stays untracked.
7. **Confirm and go**: once setup is clean, start the baseline run.

## Experimentation

Each experiment runs in the local `qlib` conda environment. The fixed harness in `prepare.py` evaluates a candidate on three rolling yearly folds and enforces a hard wall-clock budget of 10 minutes.

**What you CAN do:**
- Modify `train.py` only.
- Search in this priority order: factors, then labels, then model config, then small strategy tweaks.
- Use web research to source factor ideas, label ideas, and implementation clues, following the Web Research Policy below.
- Change factor expressions, realistic label variants, model hyperparameters, and small strategy knobs inside `train.py`, but keep the current harness interface unchanged.

**What you CANNOT do:**
- Modify `prepare.py` during the loop. It owns the evaluation harness and keep/discard logic.
- Install new packages.
- Add `vwap`-dependent factors.
- Change the market, data source contract, or evaluation fold definitions during the loop.
- Run broad grid searches or repeated local parameter sweeps on model or strategy settings.

## Fixed contract

- Market: `ashare_mainboard_no_st`
- Frequency: daily
- Input fields: `open`, `high`, `low`, `close`, `volume`, `factor`, `turnover_rate`
- Label family: default 5-day forward return, but realistic execution-aligned future-return variants are allowed inside the current single `label_expression` interface
- Strategy family: long-only TopK with dropout, with strategy redesign out of scope
- Runtime: `conda run -n qlib`

## Research Priority

- The default search order is: `factors > labels > model config > small strategy tweaks`.
- In any rolling block of 10 experiments, target at least 6 factor-focused experiments, at least 2 label-focused experiments, at most 1 model-only experiment, and at most 1 strategy-tweak experiment.
- A factor-focused experiment must add, replace, or reorganize a coherent factor family or feature interaction set. Do not count a single random extra column as factor research.
- Model-only and strategy-only experiments are allowed only as small follow-up checks after stronger factor or label ideas, not as the main search path.

## Web Research Policy

- Perform a research pass before the first non-baseline experiment.
- Perform another research pass after every 5 consecutive discards or every 10 total experiments, whichever comes first.
- Use sources in this priority order:
  1. Official Qlib docs/examples and Microsoft Qlib or RD-Agent material.
  2. Papers and working papers on factor mining, label design, and backtest overfitting.
  3. Qlib GitHub examples/issues for implementation clarifications.
  4. Broader web sources only as hypothesis generators.
- Treat docs, papers, and Qlib implementation references as evidence. Treat broader web content as tentative until validated locally by the fixed harness.
- If an experiment is externally inspired, start its description with compact tags such as `[factor][paper]`, `[label][docs]`, `[model][issue]`, or `[strategy][web]`.

## Label Policy

- The default label is a 5D forward return, but you may test realistic execution-aligned future-return variants.
- Allowed label changes are limited to what fits the current single `label_expression` interface in `train.py`.
- Valid directions include nearby horizons, open/close execution-aligned return bases, weighted horizon blends, and simple volatility-scaled or capped variants when expressible in the current formula language.
- Do not use lookahead labels, unavailable fields, or label ideas that would require new preprocessing, neutralization, or harness logic in this pass.

## Overfitting Guardrails

- Prefer new factor families, feature interactions, and label designs over hyperparameter tuning.
- Do not run back-to-back model-only experiments or back-to-back strategy-only experiments.
- Do not run local sweeps on `learning_rate`, `num_leaves`, `n_estimators`, `topk`, `n_drop`, or cost settings unless the immediately preceding kept result came from a new factor or label idea and there is a specific follow-up hypothesis.
- Keep the current TopkDropout family as the anchor. Limit strategy variation to rare local tweaks around `topk`, `n_drop`, and cost sanity checks.
- Qlib documents that turnover is directly related to `Drop / K`, so repeated tuning of these knobs is treated as overfitting risk rather than a primary research direction.

## Output format

At the end of each run, the harness prints:

```text
---
status:           keep|discard|crash
mean_sharpe:      ...
mean_rank_ic:     ...
mean_turnover:    ...
mean_max_drawdown:...
mean_annual_return:...
runtime_seconds:  ...
description:      ...
```

It also writes:

- `run.json` with the full machine-readable summary
- `results.tsv` with the compact experiment ledger

## Logging results

The TSV has seven columns:

```text
commit	sharpe	rank_ic	turnover	max_drawdown	status	description
```

`results.tsv` stays untracked by git.

If the idea is externally inspired, begin the description with compact evidence tags such as `[factor][paper]` or `[label][docs]`. Keep descriptions short and TSV-safe.

## The experiment loop

The experiment runs on a dedicated branch such as `autoresearch/mar23`.

LOOP FOREVER:

1. Look at the git state and current kept baseline.
2. If the Web Research Policy requires it, do a short research pass and extract 1-3 testable hypotheses.
3. Modify `train.py` with one experiment idea that follows the Research Priority and Overfitting Guardrails.
4. Commit the change.
5. Run:

   ```bash
   MPLCONFIGDIR=$PWD/tmp/mplconfig \
   QLIB_PROVIDER_URI=${QLIB_PROVIDER_URI:-$PWD/data/qlib_bin_daily_hfq} \
   conda run -n qlib python train.py > run.log 2>&1
   ```

6. Read the result from `run.json` or grep the summary lines from `run.log`.
7. If `status: keep`, keep the commit and advance the branch.
8. If `status: discard`, revert to the previous kept commit.
9. If `status: crash`, read the traceback in `run.log`, fix obvious bugs if the idea still makes sense, otherwise log it and move on.

## Decision rule

The harness is the source of truth.

- `keep` means the candidate beat the current kept baseline on the fixed objective.
- `discard` means it failed the objective or violated guardrails.
- `crash` means the run failed structurally or exceeded the budget.

## Simplicity criterion

All else equal, prefer smaller diffs. A tiny gain from a complicated hack is not worth much. A similar result with simpler code is valuable.

## Timeout and crashes

- If a run takes longer than 10 minutes total, treat it as a crash.
- If the provider is missing, stop and tell the human exactly what path is expected.
- If the idea crashes for a trivial reason, fix and rerun once. If it is fundamentally broken, discard it.

## NEVER STOP

Once the loop begins, do not pause to ask whether you should continue. Keep iterating until the human interrupts you.
