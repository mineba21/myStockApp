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

The current mission is to convert the Weinstein scanner from a broad candidate finder into a **strict Stan Weinstein optimal buy filter**.

---

## Source of Truth

- Use the current `main` branch as the only source of truth.
- The runnable application lives under `stock-scanner/`.
- The durable strategy reference is `docs/strategy/weinstein.md`.
- The historical implementation plan is `docs/refactor/weinstein-refactor-plan.md`.
- Ignore previous experimental branches, old PRs, and old AI-generated plans unless the user explicitly asks.
- If this file, `docs/strategy/weinstein.md`, and code behavior conflict, update the strategy document and code so they agree with this file.

Do not reuse code, assumptions, or documents from ignored branches unless explicitly requested.

---

## Plan Mode / Planning Rule

`CLAUDE.md`, `AGENTS.md`, and `docs/refactor/*.md` do not replace Plan Mode.

For broad changes, use Plan Mode if available. Broad changes include:

- Weinstein strategy logic changes
- strict buy/sell filter changes
- scanner architecture changes
- database schema changes
- web/API/UI changes
- charting features
- changes touching more than three files

For strict Weinstein filter work:

1. First produce a written plan.
2. Save it under `docs/plans/strict-weinstein-optimal-buy-filter.md`.
3. Do not implement until the plan is accepted or the user explicitly instructs implementation.
4. During implementation, do one scoped pass, run validation, summarize, then stop.

---

## Strategy Reference

This app is a stock scanner based on Stan Weinstein Stage Analysis.

Before changing scanner logic, read:

- `docs/strategy/weinstein.md`
- `docs/refactor/weinstein-refactor-plan.md`
- `stock-scanner/scanner/weinstein.py`
- `stock-scanner/scanner/scan_engine.py`
- `stock-scanner/scanner/market_analysis.py`
- `stock-scanner/config.py`
- `stock-scanner/database/models.py`
- `stock-scanner/tests/test_weinstein.py`

Do not duplicate the full strategy everywhere. Keep the “why” in `docs/strategy/weinstein.md`, the durable implementation rules in this file, and the code-level thresholds in `config.py`.

---

## Strict Weinstein Optimal Buy Filter

A strict BUY signal is valid only when **all required gates pass**.

A failed mandatory gate must block the BUY signal. It must not be downgraded into `warning_flags`, a lower grade, or a weak-quality signal.

The scanner may still compute diagnostics, but it must not save, notify, or display a failed strict BUY as a buy candidate.

---

### Gate 1 — Market Gate

Strict BUY signals require a supportive market backdrop.

Required behavior:

- `BEAR` market condition blocks all new BUY signals.
- `UNKNOWN` market condition blocks BUY signals in strict mode.
- `CAUTION` blocks BREAKOUT and RE_BREAKOUT by default.
- `CAUTION` may be allowed only through an explicit config flag, and the signal must carry a visible caution flag.
- `BULL` is the preferred market condition.
- `NEUTRAL` may be allowed only when the benchmark is not Stage 4.

Do not silently pass missing market data in strict mode.

---

### Gate 2 — Sector Gate

Strict BUY signals require sector confirmation.

Required behavior:

- The stock’s sector or industry proxy must be mapped to a sector ETF or sector index where feasible.
- The mapped sector must be `STAGE2`.
- `STAGE4` sector blocks all new BUY signals.
- `STAGE1`, `STAGE3`, `UNKNOWN`, or missing sector data must fail when `STRICT_REQUIRE_SECTOR_STAGE2=true`.
- Sector failure must be a hard filter, not a warning.

Implementation may support an explicit config override for early rollout, but the default for strict optimal filtering must require sector Stage 2.

---

### Gate 3 — Stock Weekly Stage Gate

The conceptual basis is the 30-week moving average.

Required behavior:

- Use weekly OHLCV derived deterministically from daily OHLCV when native weekly data is not available.
- Current weekly close must be above the 30-week SMA.
- 30-week SMA slope must be non-negative for Stage 1 to Stage 2 transition, and positive for mature Stage 2 continuation.
- `STAGE3` and `STAGE4` stocks must not produce BUY signals.
- Daily 150MA may be used as an approximation or secondary confirmation only.
- If both weekly 30-SMA and daily 150MA are available, weekly 30-SMA has priority.

For strict BREAKOUT, do not allow a stock below the weekly 30-SMA or daily MA150 to pass.

---

### Gate 4 — Base / Pivot Gate

Strict BREAKOUT requires a real base.

Required behavior:

- Base length must be at least `BASE_MIN_WEEKS`.
- Pivot search must not use future data.
- Base width should be at most 15%.
- Tight bases should be identified separately, for example width <= 8%.
- Current price must break above the pivot or resistance level.
- A base breakout without weekly stage confirmation must fail.
- A simple MA cross without a valid base is not a strict Weinstein BREAKOUT.

For RE_BREAKOUT, require a continuation base within Stage 2.

For REBOUND, require a real pullback/retest of prior breakout support, weekly 30-SMA, or a major moving average. MA50 rebound alone is not enough unless it has weekly/stage context.

---

### Gate 5 — Volume Gate

Breakout volume is mandatory.

Required behavior:

- BREAKOUT weekly volume must be at least `BREAKOUT_WEEKLY_VOL_RATIO`, default 2.0x.
- Daily breakout volume may use a stricter default, such as 2.0x or 3.0x, but it must be explicit in `config.py`.
- Volume failure blocks strict BREAKOUT.
- Do not convert insufficient breakout volume into only a weak grade.

For REBOUND, volume dry-up during pullback and renewed demand on rebound should be evaluated when data is available.

---

### Gate 6 — Mansfield Relative Strength Gate

Mansfield RS is a hard filter in strict mode.

Required behavior:

- Benchmark close series is required.
- Mansfield RS must be computed versus the market benchmark:
  - US: SPY unless explicitly changed.
  - KR: KOSPI200 proxy, currently 069500, unless explicitly changed.
- Current Mansfield RS must be >= 0.
- RS trend must not be falling.
- For strict BREAKOUT, require a recent RS zero-line cross or clear positive transition.
- For RE_BREAKOUT and REBOUND, require RS positive and stable/rising.

If benchmark data is unavailable, strict BUY must fail. Do not silently pass RS as “unknown”.

---

### Gate 7 — Extension / Risk Gate

Do not chase overextended breakouts.

Required behavior:

- Reject BREAKOUT when price is too extended above weekly 30-SMA or daily MA150.
- Use `BREAKOUT_MAX_EXTENDED_PCT` or a clearer weekly equivalent.
- Compute and return a predefined stop-loss level for every strict BUY signal.
- Preferred stop-loss candidates:
  - just below pivot
  - just below base low
  - just below recent swing low
  - just below weekly 30-SMA on pullback entries

A strict BUY without a stop-loss is incomplete and must fail when `STRICT_REQUIRE_STOP_LOSS=true`.

---

## Required Config Flags

Add or preserve explicit config flags for strict behavior.

Recommended defaults:

```python
STRICT_WEINSTEIN_MODE = True
STRICT_REQUIRE_MARKET_CONFIRMATION = True
STRICT_BLOCK_CAUTION_BREAKOUTS = True
STRICT_REQUIRE_SECTOR_STAGE2 = True
STRICT_REQUIRE_PRICE_ABOVE_WEEKLY_30MA = True
STRICT_REQUIRE_PRICE_ABOVE_DAILY_150MA = True
STRICT_REQUIRE_RS_POSITIVE = True
STRICT_REQUIRE_RS_RISING = True
STRICT_REQUIRE_RS_ZERO_CROSS_FOR_BREAKOUT = True
RS_ZERO_CROSS_LOOKBACK_WEEKS = 8
STRICT_REQUIRE_BREAKOUT_VOLUME = True
STRICT_REQUIRE_STOP_LOSS = True
```

Config flags may be environment-driven, but strict defaults must represent the optimal Weinstein checklist.

---

## Architecture Rules

Keep these responsibilities separate:

- data fetching: KR/US OHLCV and ticker universe loading
- sector mapping: ticker to sector/ETF/index proxy
- strategy core: pure Weinstein calculations and signal classification
- scan orchestration: applying market, sector, and strategy rules to universes
- persistence: database models and migrations
- notification: Telegram/message formatting
- web/API: routes, JSON endpoints, and templates
- charting: data endpoint and UI rendering

`scanner/weinstein.py` must remain testable with synthetic pandas data and must not fetch external data.

Do not introduce external data calls inside pure strategy functions.

---

## Implementation Rules

- Prefer small, reviewable commits and PRs.
- Do not make broad multi-file changes without a plan.
- Do not change public function signatures unless necessary and explained.
- Preserve legacy wrapper functions unless the plan explicitly replaces them.
- Do not introduce new dependencies without explaining why.
- Add or update tests for all strategy logic changes.
- External data source failures should fail gracefully.
- In strict mode, missing required confirmation data must block BUY signals.
- Do not expose secrets or environment variables in logs, templates, or client-side code.
- Keep notification formatting concise and robust.
- Avoid look-ahead bias in all scanners, signal generation, and tests.

---

## Database / Persistence Rules

When strict signal fields change, update related layers together:

- scanner result payload
- database model
- migration
- save path
- web/API serialization if applicable
- notification formatting if applicable
- tests

Recommended strict metadata fields:

- `stop_loss`
- `sector_name`
- `sector_stage`
- `rs_value`
- `rs_trend`
- `rs_zero_crossed`
- `strict_filter_passed`
- `filter_reasons` or debug-only rejected reasons

Do not overload existing fields if a new explicit field is clearer.

---

## Test Requirements

For strict Weinstein filter changes, tests must cover both pass and fail cases.

Required test categories:

- valid strict BREAKOUT passes all gates
- negative Mansfield RS blocks BUY
- falling Mansfield RS blocks BUY
- missing benchmark blocks BUY in strict mode
- no recent RS zero-cross blocks strict BREAKOUT when configured
- price below weekly 30-SMA blocks BUY
- price below daily MA150 blocks strict BREAKOUT when configured
- Stage 3 and Stage 4 block BUY
- missing/unknown sector blocks BUY when sector confirmation is required
- sector Stage 4 blocks BUY
- insufficient weekly breakout volume blocks BUY
- invalid or too-wide base blocks BUY
- overextended breakout blocks BUY
- strict BUY includes stop-loss
- no-look-ahead pivot test
- legacy SELL behavior remains valid
- market BEAR blocks all new BUY signals
- CAUTION blocks BREAKOUT when `STRICT_BLOCK_CAUTION_BREAKOUTS=true`

Use deterministic synthetic pandas fixtures wherever possible.

---

## Validation

Before claiming a task is complete, run relevant checks from `stock-scanner/`.

Typical commands:

```bash
cd stock-scanner
python -m compileall scanner database web
pytest -q
```

For focused strategy work:

```bash
cd stock-scanner
python -m compileall scanner database
pytest -q tests/test_weinstein.py
```

For scan orchestration or DB changes, also run the relevant scan-engine and model tests if present.

If a command cannot run, clearly state why and what remains unvalidated.

---

## Expected Completion Summary

Use this format at the end of each scoped task:

```md
## Changes

- ...

## Validation

- Command/result
- Command/result

## Behavior Changes

- ...

## Remaining Risks

- ...

## Suggested Next Step

- ...
```
