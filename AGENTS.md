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
