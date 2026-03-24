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

If you are on a recent Codex release with hooks support, this repo now includes repo-local Codex hooks under [.codex/config.toml](/Users/zhaozhiyu/Projects/autoresearch/.codex/config.toml) and [.codex/hooks.json](/Users/zhaozhiyu/Projects/autoresearch/.codex/hooks.json). The `SessionStart` hook injects the current branch/run state, and the `Stop` hook blocks natural stopping and tells Codex to continue the next autoresearch step.

The simplest hook-based workflow is:

1. Start Codex from this repo root.
2. Give it the normal autoresearch kickoff prompt.
3. Let the repo-local hooks keep the experiment loop moving.

If you want the current session to stop normally, create `.codex/allow_stop` in the repo root or start Codex with `AUTORESEARCH_ALLOW_STOP=1`. Remove `.codex/allow_stop` afterwards to re-enable the forever-loop behavior.

For web search, the recommended default is the Codex cached search index. Live search and shell-level network access are separate switches:

- built-in web search: `cached` by default, optionally `live` or `disabled`
- shell command network access: still off by default unless you enable it explicitly

The recommended local launcher is:

```bash
./scripts/run_codex_autoresearch.sh --model gpt-5.4
```

If your shell cannot find `codex`, the launcher will also try the default macOS app-bundle path `/Applications/Codex.app/Contents/Resources/codex`. You can override discovery explicitly with `CODEX_BIN=/absolute/path/to/codex`.

Useful variants:

```bash
# Run 5 iterations, then stop
./scripts/run_codex_autoresearch.sh --iterations 5

# Use live web search for the research passes
./scripts/run_codex_autoresearch.sh --web-search live

# Allow shell commands such as curl/pip/external APIs to use the network too
./scripts/run_codex_autoresearch.sh --allow-shell-network

# Allow Codex to write .git after approval prompts
./scripts/run_codex_autoresearch.sh --sandbox-mode danger-full-access --approval-policy on-request

# Run unattended with full access
./scripts/run_codex_autoresearch.sh --sandbox-mode danger-full-access --approval-policy never

# Force a fresh first session instead of resuming the latest one
./scripts/run_codex_autoresearch.sh --fresh

# Only if you are already inside an external sandbox and want zero approval friction
./scripts/run_codex_autoresearch.sh --dangerous
```

Stop the run with `Ctrl-C`. Progress is tracked in git, `results.tsv`, `run.json`, and `run.log`.

The launcher uses `-c 'web_search="..."'` so it works cleanly with both `codex exec` and `codex exec resume`. On some Codex CLI versions, `--search` is a top-level flag rather than an `exec` subcommand flag, so the config form is the more stable choice for this launcher.

By default the launcher uses `workspace-write` plus `approval_policy="on-request"`. That is the safe default, but `.git` remains protected in that sandbox. If you need Codex itself to perform git writes without hitting the protected-path sandbox, switch to `--sandbox-mode danger-full-access`. Keep `--approval-policy on-request` if you want prompts, or set `--approval-policy never` for unattended runs.

If you want to kick off a single interactive session manually, point it at `program.md`.

The launcher scripts explicitly disable repo-local hooks with `--disable codex_hooks`, because they already implement their own run-loop behavior. Use a normal Codex session from the repo root if you want the new hook-based forever loop.

## Containerized Full Access

For local macOS use, the cleanest high-permission setup is to run Codex inside a Docker container that mounts only this experiment repo. That keeps `danger-full-access` or `--dangerously-bypass-approvals-and-sandbox` scoped to the mounted worktree instead of your full host filesystem.

This repo includes:

- [docker/codex-autoresearch.Dockerfile](/Users/zhaozhiyu/Projects/autoresearch/docker/codex-autoresearch.Dockerfile) — Ubuntu + Miniforge + `qlib` env + Codex CLI
- [scripts/run_codex_autoresearch_docker.sh](/Users/zhaozhiyu/Projects/autoresearch/scripts/run_codex_autoresearch_docker.sh) — repo-scoped `docker run` wrapper

Build the image:

```bash
./scripts/run_codex_autoresearch_docker.sh --build-only
docker build -f docker/codex-autoresearch.Dockerfile -t autoresearch-codex:latest docker
```

Recommended unattended container run:

```bash
./scripts/run_codex_autoresearch_docker.sh --build -- \
  --model gpt-5.4 \
  --web-search live \
  --sandbox-mode danger-full-access \
  --approval-policy never
```

If you want prompts inside the container instead of unattended full access:

```bash
./scripts/run_codex_autoresearch_docker.sh -- \
  --model gpt-5.4 \
  --web-search live \
  --sandbox-mode danger-full-access \
  --approval-policy on-request
```

The Docker wrapper mounts only this repository into `/workspace` by default. It also mounts `~/.codex` if present so the containerized Codex CLI can reuse your local login cache. For headless/container flows, Codex authentication docs recommend file-based auth cache reuse and note that `~/.codex/auth.json` contains access tokens, so treat it like a password and do not commit it.

If your host Codex login is currently stored in the macOS keychain rather than in `~/.codex/auth.json`, switch to file-based credential storage before using the container:

```toml
cli_auth_credentials_store = "file"
```

Then run `codex login` on the host once and mount `~/.codex` into the container.

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
5. After baseline verification, let it continue the experiment loop defined in `program.md`.

`program.md` is the operating manual for the experiment loop. The agent should follow it rather than inventing its own workflow.

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

Each candidate runs on five fixed rolling folds:

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
