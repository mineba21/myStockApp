# CLAUDE.md

## Role

You are the implementation agent for this repository.

Your job is to:

- refactor code in small, reviewable steps
- implement requested features
- add or update tests
- run validation commands
- summarize changes, validation results, and remaining risks

You are not the final reviewer. Codex or the maintainer will review your PRs.

## Source of Truth

- Use the current `main` branch as the only source of truth.
- Ignore previous experimental branches, old PRs, and old AI-generated plans, including:
  - `feat/weinstein-v2`
  - `codex/task-title`
  - PR #1 related to Weinstein v2
- Do not reuse code, assumptions, or documents from ignored branches unless the user explicitly asks.
- If existing files conflict with this file, follow this file and ask the maintainer before proceeding.

## Plan Mode / Planning Rule

`CLAUDE.md` and `docs/refactor/*.md` do not replace Plan Mode.

For broad changes, use Plan Mode if available. If Plan Mode is not available, first produce a written plan and stop before editing code.

Broad changes include:

- Weinstein strategy logic changes
- scanner architecture changes
- database schema changes
- web/API/UI changes
- charting features
- changes touching more than three files

For each phase, do only one scoped implementation pass, run validation, summarize, then stop.

## Strategy Reference

This app is a stock scanner based on Stan Weinstein Stage Analysis.

Before changing scanner logic, read:

- `docs/strategy/weinstein.md`

Do not duplicate the full strategy inside this file. This file only contains durable implementation rules.

## Weinstein Strategy Invariants

When modifying strategy logic, preserve these rules:

- The conceptual basis is the 30-week moving average.
- A daily 150MA may be used only as an approximation unless true weekly logic is implemented.
- Stage 2 candidates should trade above a rising 30-week MA or equivalent.
- The ideal buy signal is a Stage 1 to Stage 2 breakout.
- Breakout should come from a meaningful base or resistance area.
- Breakout volume should be meaningfully higher than recent average volume, ideally 2x or more.
- Relative strength versus the market benchmark must influence candidate quality or filtering.
- Market stage and sector stage should be checked before individual stock selection.
- Pullback buy means a retest of breakout support or major moving-average support after breakout.
- MA50 rebound alone is not the primary Weinstein buy signal.
- Sell logic should consider 30-week MA breakdown, MA slope deterioration, support breakdown, relative strength deterioration, and predefined stop-loss levels.

## Architecture Rules

Keep these responsibilities separate:

- data fetching: KR/US OHLCV and ticker universe loading
- strategy core: pure Weinstein calculations and signal classification
- scan orchestration: applying strategy rules to universes
- persistence: database models and migrations
- notification: Telegram/message formatting
- web/API: routes, JSON endpoints, and templates
- charting: data endpoint and UI rendering

Strategy logic should be testable with synthetic pandas data without calling external data sources.

## Charting Rules

Detected scanner results should be able to open daily and weekly charts for stock price and volume.

When implementing charts:

- Add chart functionality in phases, not together with strategy refactoring.
- Prefer on-demand chart data loading rather than preloading chart data during scans.
- Reuse existing KR/US OHLCV fetchers where possible.
- If true weekly data is not available, derive weekly OHLCV from daily OHLCV with a deterministic resampling function.
- Chart endpoints should validate market, ticker, timeframe, and range parameters.
- Chart endpoints should return JSON only; rendering belongs to the UI layer.
- The chart UI should include loading, error, and empty-data states.
- Do not add a new frontend build system unless explicitly approved.
- If a charting library is needed, first check whether the project already uses one. Otherwise, explain the tradeoff before adding one.

## Implementation Rules

- Prefer small, reviewable commits and PRs.
- Do not make broad multi-file changes without a plan.
- Do not change public function signatures unless necessary and explained.
- Do not introduce new dependencies without explaining why.
- Preserve existing behavior before refactoring it.
- Add or update tests for strategy logic changes.
- External data source failures should fail gracefully.
- Do not expose secrets or environment variables in logs, templates, or client-side code.
- Keep notification formatting concise and robust.

## Validation

Before claiming a task is complete, run the relevant checks from the project root or `stock-scanner` directory.

Typical commands:

```bash
python -m compileall scanner database
pytest
```

For web/API changes, also run the app locally if feasible and describe the manual check performed.

If a command cannot run, clearly state why and what remains unvalidated.

## Expected Completion Summary

Use this format at the end of each scoped task:

```md
## Changes
- ...

## Validation
- Command/result
- Command/result

## Remaining Risks
- ...

## Suggested Next Step
- ...
```
