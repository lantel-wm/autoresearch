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

Each experiment runs in the local `qlib` conda environment. The fixed harness in `prepare.py` evaluates a candidate on five rolling yearly folds and enforces a hard wall-clock budget of 10 minutes.
It computes metrics, baseline-relative deltas, and last-resort hard rejects.
The final `keep` / `discard` choice is made by the LLM, not by a fixed threshold block in the harness.

**What you CAN do:**
- Modify `train.py` only.
- Choose the next experiment direction adaptively from factors, labels, model config, and small strategy tweaks.
- Use web research to source factor ideas, label ideas, and implementation clues, following the Web Research Policy below.
- Change factor expressions, realistic label variants, model hyperparameters, and small strategy knobs inside `train.py`, but keep the current harness interface unchanged.

**What you CANNOT do:**
- Modify `prepare.py` during the loop. It owns the evaluation harness and the hard safety floors.
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
- Treat that order as a prior, not a hard quota. You may shift direction when recent results imply the current search lane is saturated or when another lane has a clearer hypothesis.
- A factor-focused experiment must add, replace, or reorganize a coherent factor family or feature interaction set. Do not count a single random extra column as factor research.
- Model-only and strategy-only experiments are allowed only as small follow-up checks after stronger factor or label ideas, not as the main search path.

## Web Research Policy

- If built-in web search is available, decide for yourself when a research pass is worth doing. Triggers include the first non-baseline experiment, search stagnation, repeated discards, or uncertainty about the next direction.
- Search scope is not limited to Qlib. You may use any relevant A-share factor source you can find, including:
  1. Official Qlib docs/examples and Microsoft Qlib or RD-Agent material.
  2. Papers and working papers on A-share factors, overnight/intraday effects, liquidity, volatility, and label design.
  3. Qlib GitHub examples/issues and broader quant implementation references.
  4. Sell-side, practitioner, forum, and blog discussions on A-share factor ideas, treated as hypothesis generators rather than evidence.
- Treat docs, papers, and implementation references as stronger evidence. Treat broader web content as tentative until validated locally by the fixed harness.
- If an experiment is externally inspired, start its description with compact tags such as `[factor][paper]`, `[label][docs]`, `[model][issue]`, or `[strategy][web]`.

## Label Policy

- The default label is a 5D forward return, but you may test realistic execution-aligned future-return variants.
- Allowed label changes are limited to what fits the current single `label_expression` interface in `train.py`.
- Valid directions include nearby horizons, open/close execution-aligned return bases, weighted horizon blends, and simple volatility-scaled or capped variants when expressible in the current formula language.
- Do not use lookahead labels, unavailable fields, or label ideas that would require new preprocessing, neutralization, or harness logic in this pass.

## Overfitting Guardrails

- Prefer new factor families, feature interactions, and label designs over hyperparameter tuning, but choose the lane that currently has the clearest next hypothesis.
- Do not run back-to-back model-only experiments or back-to-back strategy-only experiments.
- Do not run local sweeps on `learning_rate`, `num_leaves`, `n_estimators`, `topk`, `n_drop`, or cost settings unless the immediately preceding kept result came from a new factor or label idea and there is a specific follow-up hypothesis.
- Keep the current TopkDropout family as the anchor. Limit strategy variation to rare local tweaks around `topk`, `n_drop`, and cost sanity checks.
- Qlib documents that turnover is directly related to `Drop / K`, so repeated tuning of these knobs is treated as overfitting risk rather than a primary research direction.
- If the latest keep's direct local neighborhood has mostly been tested and rejected, do not stop merely because the nearest neighbors look exhausted. In that case, explicitly relax the local-search policy and do a broader factor-mining pass inside `train.py`, while still staying inside the existing daily-data contract and landing on one concrete hypothesis per run.

## Output format

At the end of each run, the harness prints:

```text
---
status:           candidate|hard_reject|crash
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

Immediately after `train.py` finishes, those are provisional:

- `candidate` means the run is structurally valid and the LLM must decide `keep` or `discard`.
- `hard_reject` means the run violated a last-resort safety floor such as non-positive RankIC or an extreme turnover / drawdown blowup.
- `crash` means the run failed structurally.

During autonomous runs, the supervisor rewrites the latest row and `run.json` to the final `keep` / `discard` status after the LLM judges the tradeoff.

## Logging results

The TSV has seven columns:

```text
commit	sharpe	rank_ic	turnover	max_drawdown	status	description
```

`results.tsv` stays untracked by git.

If the idea is externally inspired, begin the description with compact evidence tags such as `[factor][paper]` or `[label][docs]`. Keep descriptions short and TSV-safe.

## The experiment loop

The experiment runs on a dedicated branch such as `autoresearch/mar23`.

Repeat this cycle:

1. Look at the git state and current kept baseline.
2. If built-in web search is enabled and you judge that outside research would materially improve the next choice, do a short research pass and extract 1-3 testable hypotheses.
3. If built-in web search is disabled in the current Codex mode, note that limitation briefly and continue with the best local hypothesis from the existing repo state.
4. If the direct local neighborhood around the latest keep looks exhausted, broaden the search before declaring a blocker. Stay within the same daily-data contract, but allow yourself a wider factor-mining pass inside `train.py`.
5. Modify `train.py` with one experiment idea that follows the Research Priority and Overfitting Guardrails.
6. Commit the change.
7. Run:

   ```bash
   MPLCONFIGDIR=$PWD/tmp/mplconfig \
   QLIB_PROVIDER_URI=${QLIB_PROVIDER_URI:-$PWD/data/qlib_bin_daily_hfq} \
   conda run -n qlib python train.py > run.log 2>&1
   ```

8. Read the result from `run.json` or grep the summary lines from `run.log`.
9. If the harness status is `candidate`, compare it against the current kept baseline and decide `keep` or `discard` yourself. Use the full tradeoff, not a single fixed threshold.
10. Also decide the experiment category yourself from `factor|label|model|strategy|baseline|other`.
11. Finalize the latest provisional result before changing git state:

   ```bash
   python3 scripts/codex_supervisor_state.py finalize-result --repo-root . --decision keep|discard --category factor|label|model|strategy|baseline|other --reason "short reason"
   ```

12. If the final decision is `keep`, keep the commit and advance the branch.
13. If the final decision is `discard`, revert to the previous kept commit.
14. If `status: crash`, read the traceback in `run.log`, fix obvious bugs if the idea still makes sense, otherwise log it and move on.
15. Move on to the next experiment from the resulting clean state.

## Decision rule

The harness is the source of truth for metrics, baselines, and last-resort safety floors.
The LLM is the source of truth for the final `keep` / `discard` choice on normal candidates.

- `candidate` means the run is structurally valid and needs LLM judgment.
- `hard_reject` means the run failed a last-resort safety floor and should normally be discarded.
- `keep` means the LLM judged that the candidate beat the current kept baseline on the total tradeoff.
- `discard` means the LLM judged that it did not, or the run violated a hard floor.
- `crash` means the run failed structurally or exceeded the budget.
- Exhausting the immediate local neighborhood is not by itself a blocker. Before you report a blocker, try at least one broader factor-mining step that still stays inside the existing daily-data contract.

## Simplicity criterion

All else equal, prefer smaller diffs. A tiny gain from a complicated hack is not worth much. A similar result with simpler code is valuable.

## Timeout and crashes

- If a run takes longer than 10 minutes total, treat it as a crash.
- If the provider is missing, stop and tell the human exactly what path is expected.
- If the idea crashes for a trivial reason, fix and rerun once. If it is fundamentally broken, discard it.

## Continuation

Once the loop begins, keep iterating until the human interrupts you.
