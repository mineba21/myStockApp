# AGENTS.md

## Purpose

This file guides review agents, especially Codex, when reviewing changes in this repository.

For the strict Weinstein task, Codex is the reviewer, not the implementation agent.

The reviewer must verify whether the implementation truly converts the scanner from a broad Weinstein-style candidate finder into a strict Stan Weinstein optimal buy filter.

---

## Repository Expectations

- The runnable application lives under `stock-scanner/`.
- Unless a task explicitly says otherwise, treat `stock-scanner/` as the application root.
- Do not rewrite the whole app.
- Keep review feedback focused, actionable, and tied to the requested task.
- Avoid unrelated refactor suggestions.
- Prefer correctness, no-look-ahead behavior, test coverage, and safe migrations over style-only feedback.

---

## First Files to Read

Before reviewing strict Weinstein changes, read:

1. `CLAUDE.md`
2. `docs/strategy/weinstein.md`
3. `docs/refactor/weinstein-refactor-plan.md`
4. `docs/plans/strict-weinstein-optimal-buy-filter.md`, if present

Then inspect at minimum:

- `stock-scanner/config.py`
- `stock-scanner/scanner/weinstein.py`
- `stock-scanner/scanner/scan_engine.py`
- `stock-scanner/scanner/market_analysis.py`
- `stock-scanner/scanner/us_stocks.py`
- `stock-scanner/scanner/kr_stocks.py`
- `stock-scanner/database/models.py`
- `stock-scanner/tests/test_weinstein.py`

If DB, API, notification, or UI behavior changed, also inspect:

- `stock-scanner/web/app.py`
- `stock-scanner/web/templates/index.html`
- `stock-scanner/notifications/telegram.py`
- any new or changed tests

---

## Review Target

The target behavior is:

A BUY signal is saved, notified, and displayed only when all mandatory strict Weinstein gates pass.

Mandatory gates:

1. market confirmation
2. sector Stage 2 confirmation
3. weekly stock Stage confirmation using 30-week SMA
4. base/pivot breakout or valid Stage 2 pullback setup
5. breakout volume confirmation
6. Mansfield RS positive/rising confirmation
7. overextension/risk check
8. predefined stop-loss

A failed mandatory gate must block the BUY signal.

It is not acceptable for a failed mandatory gate to become only:

- `warning_flags`
- lower grade
- weak quality
- notification note
- debug-only text
- silently ignored missing data

---

## Strict Weinstein Review Checklist

### 1. Market Gate

Verify:

- `BEAR` blocks all new BUY signals.
- `UNKNOWN` does not silently pass in strict mode.
- `CAUTION` behavior is explicit and config-driven.
- Market filter applies before save/notification.
- Market filter applies consistently to KR and US scans.

Blocker examples:

- BEAR market still allows BUY.
- Missing market condition passes without explicit config.
- Market filtering is only reflected in message text.

---

### 2. Sector Gate

Verify:

- Sector stage is used as a per-stock BUY gate when `STRICT_REQUIRE_SECTOR_STAGE2=true`.
- Sector `STAGE2` is required for strict BUY.
- Sector `STAGE4` blocks BUY.
- Unknown or missing sector blocks BUY when strict sector requirement is enabled.
- Sector failure is not downgraded to warning-only behavior.
- Sector context is included in signal metadata when available.

Blocker examples:

- Sector ETF status is still only used in notification summary.
- Unknown sector passes by default in strict mode.
- Sector Stage 4 stock can still be saved as BUY.

---

### 3. Weekly Stage / 30-Week MA Gate

Verify:

- Weekly 30-SMA is the primary conceptual gate.
- Current weekly close must be above weekly 30-SMA.
- 30-week SMA slope must be non-negative or rising according to the signal type.
- Stage 3 and Stage 4 do not produce BUY signals.
- Daily MA150 is treated as approximation/secondary confirmation.
- Strict BREAKOUT below daily MA150 fails when configured.

Blocker examples:

- BREAKOUT can pass below weekly 30-SMA.
- BREAKOUT can pass below daily MA150 while strict MA150 confirmation is enabled.
- Stage 3 or Stage 4 produces BUY.

---

### 4. Base / Pivot Gate

Verify:

- BREAKOUT requires a valid base/resistance area.
- Base length and width are enforced.
- Pivot calculation avoids look-ahead bias.
- Current price actually breaks the pivot.
- RE_BREAKOUT requires a continuation base.
- REBOUND requires Stage 2 context and a real retest of support or major MA.

Blocker examples:

- Simple MA cross is labeled BREAKOUT without valid base.
- Base uses future bars.
- MA50 bounce alone is treated as primary Weinstein BUY.

---

### 5. Volume Gate

Verify:

- BREAKOUT volume threshold is enforced.
- Weekly breakout volume default is at least 2.0x unless explicitly justified.
- Daily breakout volume threshold is explicit.
- Insufficient volume blocks strict BREAKOUT.
- Volume failure is not merely a weak grade.

Blocker examples:

- Breakout with volume below threshold still saved.
- Volume ratio missing but BUY still passes.
- Volume failure only lowers `signal_quality`.

---

### 6. Mansfield RS Gate

Verify:

- Benchmark data is required in strict mode.
- Current Mansfield RS must be >= 0.
- RS trend must be rising or non-falling according to signal type.
- Strict BREAKOUT requires recent RS zero-line cross when configured.
- Missing benchmark data blocks strict BUY.
- Negative RS blocks strict BUY.
- Falling RS blocks strict BUY.

Blocker examples:

- `rs_value < 0` still saved as BUY.
- Missing benchmark passes as “unknown”.
- RS failure is only added to `warning_flags`.
- RS is computed but not used as a hard filter.

---

### 7. Extension / Risk / Stop-Loss Gate

Verify:

- Overextended BREAKOUT is rejected.
- Strict BUY includes a deterministic `stop_loss`.
- `stop_loss` is persisted or exposed where the signal is saved/displayed.
- Missing `stop_loss` fails strict BUY when configured.
- Stop-loss rule is tested.

Blocker examples:

- Strict BUY saved without stop-loss.
- Stop-loss computed but not saved.
- Stop-loss depends on future data.

---

## DB and Persistence Review

If signal fields changed, verify:

- `database/models.py` includes new nullable columns where needed.
- `_migrate()` adds columns safely for existing SQLite DBs.
- `_save()` handles both insert and update paths.
- Existing DB rows do not break app startup.
- Field names are explicit and not overloaded.

Recommended fields to check:

- `stop_loss`
- `sector_name`
- `sector_stage`
- `rs_trend`
- `rs_zero_crossed`
- `strict_filter_passed`

Blocker examples:

- New signal fields exist in payload but are not persisted.
- Insert path updated but update path not updated.
- Migration missing.
- Migration is destructive.

---

## Test Review

Required false-positive tests:

- negative Mansfield RS blocks BUY
- falling Mansfield RS blocks BUY
- missing benchmark blocks BUY in strict mode
- no recent RS zero-cross blocks strict BREAKOUT when configured
- price below weekly 30-SMA blocks BUY
- price below daily MA150 blocks BREAKOUT when configured
- Stage 3 blocks BUY
- Stage 4 blocks BUY
- sector Stage 4 blocks BUY
- unknown sector blocks BUY when required
- insufficient breakout volume blocks BUY
- invalid base blocks BUY
- too-wide base blocks BUY
- overextended BREAKOUT blocks BUY
- missing stop-loss blocks strict BUY when configured
- BEAR market blocks BUY
- CAUTION blocks BREAKOUT when configured

Required positive tests:

- valid strict BREAKOUT passes all gates
- valid Stage 2 RE_BREAKOUT passes all gates
- valid Stage 2 REBOUND passes all gates
- strict BUY includes stop-loss
- strict BUY includes sector metadata where available
- strict BUY includes RS metadata

Regression tests:

- existing SELL tests still pass
- weekly OHLCV conversion still deterministic
- no-look-ahead pivot test passes
- scan orchestration still handles external data failure gracefully

Blocker examples:

- tests cover only happy paths
- no test proves RS is a hard filter
- no test proves sector is a hard filter
- no test proves missing data fails in strict mode
- tests depend on live yfinance, pykrx, or FDR calls for core logic

---

## No-Look-Ahead Review

Verify:

- Pivot/base detection uses only bars available at signal time.
- RS zero-cross uses only historical/current data.
- Weekly resampling does not use future dates.
- Tests include at least one no-look-ahead case.

Blocker examples:

- Pivot uses the current breakout bar as part of the base high incorrectly.
- Base detection includes future bars.
- RS crossing is checked with future values.

---

## Configuration Review

Verify:

- Strict config flags are explicit.
- Defaults match strict optimal filtering.
- Environment variable parsing is safe.
- Disabling a strict gate requires explicit config.
- Config names are understandable.

Expected flags include:

- `STRICT_WEINSTEIN_MODE`
- `STRICT_REQUIRE_MARKET_CONFIRMATION`
- `STRICT_BLOCK_CAUTION_BREAKOUTS`
- `STRICT_REQUIRE_SECTOR_STAGE2`
- `STRICT_REQUIRE_PRICE_ABOVE_WEEKLY_30MA`
- `STRICT_REQUIRE_PRICE_ABOVE_DAILY_150MA`
- `STRICT_REQUIRE_RS_POSITIVE`
- `STRICT_REQUIRE_RS_RISING`
- `STRICT_REQUIRE_RS_ZERO_CROSS_FOR_BREAKOUT`
- `RS_ZERO_CROSS_LOOKBACK_WEEKS`
- `STRICT_REQUIRE_BREAKOUT_VOLUME`
- `STRICT_REQUIRE_STOP_LOSS`

Blocker examples:

- Strict defaults are loose.
- Config says strict but code treats failures as warnings.
- Required config is added but not used.

---

## Validation Commands

Reviewers should expect implementation to run from `stock-scanner/`:

```bash
python -m compileall scanner database web
pytest -q
```

For focused strategy-only changes:

```bash
python -m compileall scanner database
pytest -q tests/test_weinstein.py
```

If these were not run, the reviewer must call that out.

If commands fail due to local environment issues, the implementation summary must clearly state what was not validated.

---

## Review Output Format

Use this format:

```md
## Verdict

- PASS / PASS WITH MINOR ISSUES / BLOCKED

## Blockers

- ...

## Non-blocking Issues

- ...

## Strict Weinstein Gate Review

- Market:
- Sector:
- Weekly Stage:
- Base/Pivot:
- Volume:
- Mansfield RS:
- Extension/Stop-loss:

## Test Review

- ...

## DB / Persistence Review

- ...

## No-Look-Ahead Review

- ...

## Validation Evidence

- Commands reviewed:
- Results:

## Recommended Fixes

- ...
```

---

## Automatic Blocker Conditions

Mark the review as `BLOCKED` if any of these are true:

- Negative Mansfield RS can still produce BUY.
- Falling Mansfield RS can still produce BUY.
- Missing benchmark can still produce strict BUY.
- Sector Stage 4 can still produce BUY when sector confirmation is required.
- Unknown sector can pass strict mode without explicit override.
- BREAKOUT can pass below weekly 30-SMA.
- Stage 3 or Stage 4 can produce BUY.
- Insufficient breakout volume can produce strict BREAKOUT.
- Strict BUY can be saved without stop-loss.
- Mandatory gate failures are only warning flags.
- Tests do not cover false positives.
- Implementation introduces look-ahead bias.
- Strategy core fetches external data.
- DB schema changed without safe migration.
