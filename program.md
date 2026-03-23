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
- Change factor expressions, model hyperparameters, label variants, and strategy knobs inside `train.py`.

**What you CANNOT do:**
- Modify `prepare.py` during the loop. It owns the evaluation harness and keep/discard logic.
- Install new packages.
- Add `vwap`-dependent factors.
- Change the market, data source contract, or evaluation fold definitions during the loop.

## Fixed contract

- Market: `ashare_mainboard_no_st`
- Frequency: daily
- Input fields: `open`, `high`, `low`, `close`, `volume`, `factor`, `turnover_rate`
- Label family: 5-day forward return
- Strategy family: long-only TopK with dropout
- Runtime: `conda run -n qlib`

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

## The experiment loop

The experiment runs on a dedicated branch such as `autoresearch/mar23`.

LOOP FOREVER:

1. Look at the git state and current kept baseline.
2. Modify `train.py` with one experiment idea.
3. Commit the change.
4. Run:

   ```bash
   MPLCONFIGDIR=$PWD/tmp/mplconfig \
   QLIB_PROVIDER_URI=${QLIB_PROVIDER_URI:-$PWD/data/qlib_bin_daily_hfq} \
   conda run -n qlib python train.py > run.log 2>&1
   ```

5. Read the result from `run.json` or grep the summary lines from `run.log`.
6. If `status: keep`, keep the commit and advance the branch.
7. If `status: discard`, revert to the previous kept commit.
8. If `status: crash`, read the traceback in `run.log`, fix obvious bugs if the idea still makes sense, otherwise log it and move on.

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
