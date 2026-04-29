# Strict Weinstein Optimal Buy Filter Plan

## 1. Current State Summary

The scanner already implements a Weinstein-style workflow:

- weekly OHLCV conversion
- weekly 30-SMA and 10-SMA indicators
- stage classification
- Mansfield RS calculation
- base pivot detection
- BREAKOUT / RE_BREAKOUT / REBOUND buy signals
- SELL checks
- market condition filtering
- sector ETF analysis summary

However, the current behavior is closer to a candidate finder than a strict optimal buy filter.

Known gaps to close:

- Negative or weak Mansfield RS may still appear as a signal with warning metadata.
- Sector Stage is analyzed but not used as a hard per-stock BUY gate.
- Some BREAKOUT cases may pass before price/stage confirmation is strict enough.
- Stop-loss is not guaranteed as part of every strict BUY payload.
- Strategy docs and implementation rules need to say clearly that strict required gates are hard filters.

## 2. Goal

Convert the scanner into a strict Weinstein optimal buy filter.

A BUY signal should be saved, notified, and displayed only when all required Weinstein gates pass:

1. market confirmation
2. sector confirmation
3. weekly stock stage confirmation
4. base/pivot breakout or valid Stage 2 pullback setup
5. breakout volume confirmation
6. Mansfield RS positive/rising confirmation
7. overextension/risk check
8. predefined stop-loss

## 3. Non-Goals

This task must not:

- rewrite the whole application
- replace the existing FastAPI UI
- introduce a new frontend build system
- add broad backtesting infrastructure
- add unrelated portfolio/accounting features
- change external data providers unless necessary
- remove legacy helper functions without a specific reason
- silently change SELL semantics outside the strict-filter scope

## 4. Files to Read First

Read these before editing:

- `CLAUDE.md`
- `AGENTS.md`
- `docs/strategy/weinstein.md`
- `docs/refactor/weinstein-refactor-plan.md`
- `stock-scanner/config.py`
- `stock-scanner/scanner/weinstein.py`
- `stock-scanner/scanner/scan_engine.py`
- `stock-scanner/scanner/market_analysis.py`
- `stock-scanner/scanner/us_stocks.py`
- `stock-scanner/scanner/kr_stocks.py`
- `stock-scanner/database/models.py`
- `stock-scanner/tests/test_weinstein.py`

## 5. Design Decisions

### 5.1 Strict Mode

Add explicit config flags, defaulting to strict behavior:

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

Strict mode means failed required gates return no BUY signal.

### 5.2 Hard Gates vs Warnings

Mandatory Weinstein gates must be hard filters:

- market BEAR
- missing benchmark in strict mode
- negative Mansfield RS
- falling Mansfield RS
- missing/unknown sector when sector confirmation is required
- sector not Stage 2
- price below weekly 30-SMA
- price below daily 150MA when configured
- Stage 3 or Stage 4
- invalid base
- insufficient breakout volume
- missing stop-loss

`warning_flags` may remain only for non-fatal diagnostics.

### 5.3 Pure Strategy Core

`scanner/weinstein.py` should remain pure.

It may accept:

- daily OHLCV
- benchmark close series
- market condition
- sector stage/name context
- config-derived flags

It must not fetch data.

### 5.4 Sector Confirmation

Implement sector confirmation in scan orchestration or a small dedicated helper module.

Preferred approach:

- Keep sector ETF stage calculation in `market_analysis.py`.
- Add a clear helper that resolves stock sector context:
  - US: use sector data from S&P500/NASDAQ100 universe where available.
  - KR: use available market/industry proxy if available; otherwise treat as unknown in strict mode.
- Add `sector_name` and `sector_stage` to signal payload.
- If `STRICT_REQUIRE_SECTOR_STAGE2=true` and sector is unknown, block BUY.

Do not silently pass missing sector information.

### 5.5 Mansfield RS Confirmation

Enhance RS logic so it can answer:

- current Mansfield RS value
- RS trend direction
- whether RS crossed above zero recently
- whether RS data is unavailable

For strict BREAKOUT:

- benchmark data required
- current RS >= 0
- RS trend rising or non-falling
- recent zero-line cross required when configured

For RE_BREAKOUT / REBOUND:

- benchmark data required
- current RS >= 0
- RS trend non-falling

### 5.6 Stop-Loss

Every strict BUY must include `stop_loss`.

Suggested derivation:

- BREAKOUT: below pivot or base low
- RE_BREAKOUT: below continuation base low
- REBOUND: below pullback low or weekly 30-SMA support

Use a deterministic rule and add tests.

### 5.7 Persistence

If new fields are returned in strict signals, update:

- `database/models.py`
- `_migrate()`
- `_save()` in `scan_engine.py`
- web/API serialization if it reads model columns
- notification formatting if helpful

Recommended DB fields:

- `stop_loss`
- `sector_name`
- `sector_stage`
- `rs_trend`
- `rs_zero_crossed`
- `strict_filter_passed`

Do not store failed BUY candidates as normal `ScanResult` rows.

## 6. Implementation Phases

### Phase 0 — Documentation Alignment

Files:

- `docs/strategy/weinstein.md`
- `CLAUDE.md`

Changes:

- Update strategy language from “RS/sector influence quality” to “strict mode requires RS and sector confirmation”.
- Clarify that strict optimal BUY requires all gates.
- Clarify that missing required confirmation data blocks BUY in strict mode.

Validation:

```bash
git diff -- docs/strategy/weinstein.md CLAUDE.md
```

### Phase 1 — Config and Core Gate Helpers

Files:

- `stock-scanner/config.py`
- `stock-scanner/scanner/weinstein.py`
- `stock-scanner/tests/test_weinstein.py`

Changes:

- Add strict config flags.
- Add helper for strict gate evaluation.
- Add RS helper for zero-cross and rising/non-falling trend.
- Add stop-loss helper.
- Ensure BREAKOUT checks weekly 30-SMA and daily 150MA where configured.
- Ensure failed strict gates return no BUY.

Tests:

- RS positive pass
- RS negative fail
- RS falling fail
- missing benchmark fail
- no zero-cross fail for strict BREAKOUT
- price below weekly 30-SMA fail
- price below daily MA150 fail
- valid stop-loss is returned

Validation:

```bash
cd stock-scanner
python -m compileall scanner database
pytest -q tests/test_weinstein.py
```

### Phase 2 — Sector Gate Integration

Files:

- `stock-scanner/scanner/market_analysis.py`
- `stock-scanner/scanner/us_stocks.py`
- `stock-scanner/scanner/kr_stocks.py`
- `stock-scanner/scanner/scan_engine.py`
- tests as needed

Changes:

- Expose sector stage data in a form scan orchestration can use.
- Add sector context to each stock where available.
- Pass sector context into `analyze_stock` or into a strict gate wrapper.
- Block BUY if sector is not Stage 2 when `STRICT_REQUIRE_SECTOR_STAGE2=true`.
- Unknown sector must fail in strict mode.

Tests:

- sector Stage 2 passes
- sector Stage 4 fails
- sector unknown fails when strict sector requirement is enabled
- sector unknown can pass only when requirement is explicitly disabled

Validation:

```bash
cd stock-scanner
python -m compileall scanner database
pytest -q
```

### Phase 3 — Persistence / Notification Update

Files:

- `stock-scanner/database/models.py`
- `stock-scanner/scanner/scan_engine.py`
- notification or web files if required

Changes:

- Add migration columns for new strict metadata.
- Save `stop_loss`, `sector_name`, `sector_stage`, `rs_trend`, `rs_zero_crossed` where available.
- Keep backward compatibility for existing DBs.
- Optionally show stop-loss and sector in Telegram notification.

Tests:

- model migration does not break
- `_save()` persists new fields
- existing result update path handles new fields

Validation:

```bash
cd stock-scanner
python -m compileall scanner database web
pytest -q
```

### Phase 4 — End-to-End Regression

Files:

- tests only unless bug fixes are needed

Tests:

- valid strict BREAKOUT saved
- failed RS candidate not saved
- failed sector candidate not saved
- BEAR market candidate not saved
- strict BUY contains stop-loss
- legacy SELL tests still pass
- chart/API tests still pass if present

Validation:

```bash
cd stock-scanner
python -m compileall scanner database web
pytest -q
```

## 7. Review Criteria

The implementation is acceptable only if:

- mandatory gates block failed BUYs
- RS is no longer warning-only
- sector is no longer summary-only when strict sector mode is enabled
- BREAKOUT cannot pass below weekly 30-SMA
- BREAKOUT cannot pass below daily MA150 when configured
- missing benchmark fails strict BUY
- missing stop-loss fails strict BUY when configured
- tests cover false positives, not only happy paths
- no look-ahead bias is introduced
- pure strategy code does not fetch external data

## 8. Risks

- Strict sector requirement may reduce signals sharply, especially for KR if sector mapping is incomplete.
- Missing benchmark or sector data may block many candidates.
- New DB fields require safe migration.
- More filters may require updating UI/notification expectations.

## 9. Rollback Notes

To rollback behavior without reverting code, strict mode can be disabled with config flags.

However, documentation and tests should continue to define strict optimal filtering as the intended production behavior.
