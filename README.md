# autoresearch

This is an `autoresearch`-style quant research fork.

The original idea of the repo stays intact: give an AI agent a small but real research setup, let it mutate one file, run a short comparable experiment, keep improvements, discard regressions, and repeat overnight. The difference is that the backend here is Qlib on A-share daily data instead of an LLM training loop.

## How it works

The repo is deliberately small and only has three files that matter:

- `prepare.py` — the fixed Qlib harness. It validates the local provider, loads features from Qlib, runs the rolling-fold evaluation, backtests the signal, writes `run.json`, and decides `keep` vs `discard`.
- `train.py` — the only file the agent edits. It defines the candidate experiment: mainly factor families and label expressions, with little smaller model and strategy follow-up tweaks.
- `program.md` — the human-authored instruction file that tells the autonomous agent how to operate.

The philosophy is the same as the original repo:

- one small codebase
- one file the agent mutates
- one fixed evaluation harness
- one short comparable experiment loop
- one branch that only advances when the candidate improves

## Quick start

Manual bring-up looks like this:

```bash
# 1. Work from the repo root
cd /Users/zhaozhiyu/Projects/autoresearch

# 2. Make sure the Qlib provider is available locally
#    - default path: data/qlib_bin_daily_hfq
#    - or export QLIB_PROVIDER_URI=/abs/path/to/qlib_bin_daily_hfq

# 3. Create a writable matplotlib cache dir for the conda env
mkdir -p tmp/mplconfig

# 4. Verify the provider and runtime
MPLCONFIGDIR=$PWD/tmp/mplconfig \
QLIB_PROVIDER_URI=${QLIB_PROVIDER_URI:-$PWD/data/qlib_bin_daily_hfq} \
conda run -n qlib python prepare.py --check

# 5. Run the baseline experiment
MPLCONFIGDIR=$PWD/tmp/mplconfig \
QLIB_PROVIDER_URI=${QLIB_PROVIDER_URI:-$PWD/data/qlib_bin_daily_hfq} \
conda run -n qlib python train.py > run.log 2>&1

# 6. Inspect the result
cat run.json
tail -n 40 run.log
```

After the first run you will also have an untracked `results.tsv` ledger in the repo root.

## Runtime

This fork does **not** use `uv` for experiments. Run everything in the existing `qlib` conda environment.

```bash
mkdir -p tmp/mplconfig

MPLCONFIGDIR=$PWD/tmp/mplconfig \
QLIB_PROVIDER_URI=${QLIB_PROVIDER_URI:-$PWD/data/qlib_bin_daily_hfq} \
conda run -n qlib python prepare.py --check

MPLCONFIGDIR=$PWD/tmp/mplconfig \
QLIB_PROVIDER_URI=${QLIB_PROVIDER_URI:-$PWD/data/qlib_bin_daily_hfq} \
conda run -n qlib python train.py
```

## Running the agent

To launch `autoresearch` mode, start your coding agent in this repository and point it at `program.md`.

Example kickoff prompt:

```text
Read README.md and program.md, then set up a new autoresearch quant run.
Use the local qlib conda env.
Verify data/qlib_bin_daily_hfq, create a fresh autoresearch/<tag> branch,
run the baseline, then do factor-first and label-second research.
Use web research according to program.md, avoid model/strategy grid search,
and then begin the experiment loop.
```

The intended flow is:

1. Open the agent in `/Users/zhaozhiyu/Projects/autoresearch`.
2. Let it read `README.md`, `program.md`, `prepare.py`, and `train.py`.
3. Let it create a fresh `autoresearch/<tag>` branch.
4. Let it run the baseline with output redirected to `run.log`.
5. After baseline verification, let it continue the keep/discard loop defined in `program.md`.

`program.md` is the operating manual for the autonomous loop. The agent should follow it rather than inventing its own workflow.

## Research policy

The agent is expected to optimize in this order:

- factors first
- labels second
- model config third
- small strategy tweaks last

In practice this means:

- spend most experiments generating or reorganizing coherent factor families
- use label design as the second major search direction

Web research is part of the intended workflow:

- do a research pass before the first non-baseline experiment
- repeat it after 5 consecutive discards or every 10 total experiments
- prioritize sources in this order:
  1. [Qlib docs and examples](https://qlib.readthedocs.io/en/latest/)
  2. [Microsoft Qlib / RD-Agent materials](https://github.com/microsoft/qlib)
  3. papers on factor mining, label design, and backtest overfitting
  4. broader web sources only as hypothesis generators

Useful references for the agent’s research loop:

- [Building Formulaic Alphas](https://qlib.readthedocs.io/en/latest/advanced/alpha.html)
- [Qlib Strategy Docs / TopkDropoutStrategy](https://qlib.readthedocs.io/en/latest/component/strategy.html?highlight=TopkDropoutStrategy)
- [R&D-Agent-Quant](https://www.microsoft.com/en-us/research/publication/rd-agent-quant-a-multi-agent-framework-for-data-centric-factors-and-model-joint-optimization/?lang=zh-cn)
- [The Probability of Backtest Overfitting](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253)
- [Taming the Factor Zoo](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2934020)

If an experiment comes from external research, its description should begin with compact tags such as `[factor][paper]`, `[label][docs]`, `[model][issue]`, or `[strategy][web]`.

## Data contract

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
- `vwap` is intentionally unsupported in v1

`data/` is ignored by git, so each worktree must have the provider copied in locally or set `QLIB_PROVIDER_URI` explicitly.

## Experiment design

Each candidate runs on three fixed rolling folds:

1. train `2015-2019`, valid `2020`, test `2021`
2. train `2016-2020`, valid `2021`, test `2022`
3. train `2017-2021`, valid `2022`, test `2023`
4. train `2018-2022`, valid `2023`, test `2024`
5. train `2019-2023`, valid `2024`, test `2025`

The harness enforces:

- a hard 10-minute total runtime budget
- long-only Top50 with dropout
- transaction costs
- mean net Sharpe as the primary objective
- positive mean RankIC as a guardrail

## Output artifacts

Every run produces:

- `run.log` — full stdout/stderr when you redirect output
- `run.json` — machine-readable summary
- `results.tsv` — untracked ledger of experiments

The TSV schema is:

```text
commit	sharpe	rank_ic	turnover	max_drawdown	status	description
```

## Project structure

```text
prepare.py      — fixed Qlib harness, do not modify during the loop
train.py        — the single editable experiment file
program.md      — autonomous-agent instructions
README.md       — repo overview
```

## Operating principle

This repo is intentionally narrow. It is for fast, comparable, autonomous daily-equity signal research. It is not a general trading platform, not a minute-bar framework, and not a production execution engine.
