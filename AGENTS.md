# AGENTS.md

## Repository expectations

- This repository's runnable application lives under `stock-scanner/`.
- Unless a task explicitly says otherwise, treat `stock-scanner/` as the application root and keep code changes there.
- Do not rewrite the whole app unless the task explicitly asks for it.
- Keep changes focused, reviewable, and backward-compatible where practical.
- Avoid unrelated refactors.

## First files to read

Before making non-trivial changes, read:

1. `stock-scanner/PROJECT_OVERVIEW.md`
2. `stock-scanner/docs/weinstein_scanner.md`

For Weinstein scanner tasks, inspect at minimum:

- `stock-scanner/scanner/weinstein.py`
- `stock-scanner/scanner/scan_engine.py`
- `stock-scanner/database/models.py`
- `stock-scanner/config.py`
- `stock-scanner/tests/test_weinstein.py`

## Working rules

- Preserve legacy behavior unless the task explicitly replaces it.
- Prefer introducing a clearly named v2 path, feature flag, or isolated research module over destructive replacement.
- Do not silently change public behavior, DB schema, or saved signal semantics.
- When signal fields change, update all related layers together:
  - scanner logic
  - save path
  - DB schema / migration
  - docs
  - tests
- Avoid look-ahead bias in scanners, signal generation, and backtests.

## Environment and commands

Run commands from `stock-scanner/`.

Typical setup:

```bash
cd stock-scanner
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Run tests with:

```bash
venv/bin/python -m pytest tests/ -v
```

Start the app with:

```bash
cd stock-scanner
python main.py
```

## Strategy-specific guidance

For Stan Weinstein / Stage Analysis work:

- Treat weekly stage, pivot, and Mansfield RS as first-class concepts.
- Weekly and daily concepts must not be mixed carelessly.
- If a result payload includes both legacy and v2 fields, naming and save behavior must stay explicit.
- Prefer hard filters over warning-only behavior when the task asks for stricter Weinstein compliance.
- When changing pivot logic, also update tests for false positives and no-look-ahead behavior.

## DB and persistence guidance

- Inspect `stock-scanner/database/models.py` and `_migrate()` before changing persisted fields.
- Keep DB changes backward-compatible when possible.
- If adding new columns, update migrations and tests together.
- Do not overload existing fields if separate weekly/v2 fields would make behavior clearer.

## Research / backtest guidance

- Put research code in a clearly named folder such as `stock-scanner/research/` or `stock-scanner/backtests/`.
- Put generated artifacts under `stock-scanner/outputs/`.
- Prefer reproducible plain-Python workflows with minimal dependencies.
- Do not assume internet access or external downloads.
- If real data is missing, build the harness fully and validate it with deterministic synthetic fixtures.

## Validation expectations

- Add or update tests for any non-trivial logic change.
- Run relevant tests before finishing.
- At the end of each task, report:
  - files changed
  - exact commands run
  - test results
  - schema or behavior changes
  - remaining risks / limitations



## Planning rules

For significant features, refactors, schema changes, or backtest/research work:

- Start in planning mode before editing code.
- Do not begin implementation until a written plan is produced and approved.
- Save the plan under `stock-scanner/docs/plans/<task-name>.md`.
- The plan must include:
  - current-state summary
  - goals and non-goals
  - files to change
  - design decisions
  - DB/schema impact
  - test plan
  - verification commands
  - risks / rollback notes
- If implementation reveals a major change in approach, update the plan first.
