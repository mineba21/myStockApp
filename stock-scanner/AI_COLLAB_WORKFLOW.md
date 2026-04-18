# AI Collaboration Guide (Codex + Claude Code)

This document is for team collaboration using both Codex and Claude Code.
It explains project code flow, major features, and recent updates so both agents can work consistently.

---

## 1) Project code flow

### 1.1 Data source layer
- `scanner/kr_stocks.py`
  - KR ticker universe (KOSPI/KOSDAQ)
  - KR OHLCV fetch (`FinanceDataReader` first, `pykrx` fallback)
- `scanner/us_stocks.py`
  - US universe from S&P500 + NASDAQ100 (Wikipedia)
  - US OHLCV fetch from `yfinance`

### 1.2 Signal analysis layer
- `scanner/weinstein.py`
  - Core Weinstein stage classification
  - Buy signal detection: `BREAKOUT`, `RE_BREAKOUT`, `REBOUND`
  - Sell signal detection for watchlist

### 1.3 Market regime layer
- `scanner/market_analysis.py`
  - US/KR index stage analysis
  - Sector ETF stage analysis for US/KR
  - Benchmark close provider for RS calculation

### 1.4 Orchestration layer
- `scanner/scan_engine.py`
  - Full scan execution (`KR`, `US`, `ALL`)
  - Result persistence to DB (`ScanResult`)
  - Telegram notification formatting and dispatch

### 1.5 Persistence layer
- `database/models.py`
  - SQLAlchemy models
  - DB initialization + lightweight migration hooks

### 1.6 UI/API layer
- `main.py`, `web/app.py`, `web/templates/index.html`
  - Web entrypoint, API routes, dashboard view

---

## 2) Current feature summary

- Weinstein stage-based scanning for KR/US stocks.
- RS (relative strength) support vs benchmark.
- Watchlist sell-alert monitoring.
- Telegram alerts for buy/sell signals.
- Market/sector context included in alerts.
- Scan results stored in SQLite via SQLAlchemy.

---

## 3) Recent update history (important)

### 3.1 `scanner/weinstein.py`
- Added MA50 calculation in `analyze_stock`.
- BREAKOUT quality tightened:
  - Base validation before breakout (10 sessions window, >=7 closes inside MA150 ±5%).
  - Breakout-day close quality filter (close/high >= 0.70).
- REBOUND logic changed to MA50 touch/reclaim + 1.3x volume confirmation.
- Added return fields:
  - `ma50`
  - `base_quality` (`STRONG`, `WEAK`, `NONE`, `N/A`)
- `check_sell_signal` now includes `severity`:
  - `HIGH`: MA breach or stop-loss
  - `MEDIUM`: MA150 slope positive→non-positive turn
  - `LOW`: Stage3 caution

### 3.2 `scanner/kr_stocks.py`
- Added KR universe filters:
  - Minimum market cap
  - Minimum price
  - Instrument keyword exclusions (SPAC/ETF/ETN/etc.)
- If filtering API fails, returns original unfiltered universe (safe fallback).

### 3.3 `scanner/us_stocks.py`
- Added lightweight exclusion list (`EXCLUDE_US`) while preserving symbol normalization (`.` -> `-`).

### 3.4 `scanner/market_analysis.py`
- Added US/KR sector ETFs.
- `get_market_stages()` now returns:
  - `US_SECTORS`
  - `KR_SECTORS`

### 3.5 `scanner/scan_engine.py`
- Added signal quality grading (`S`, `A`, `B`).
- Save path now persists `grade`.
- Notification now shows:
  - grade badge (🔥/✅/📌)
  - sector strength/weakness summary line

### 3.6 `database/models.py`
- Added `ScanResult.grade` column (default `B`).
- Added migration guard to add missing `grade` column for existing DB.

---

## 4) Codex + Claude Code working rules

Use this as a shared standard when either model modifies code.

1. **Do not change external function signatures** unless explicitly requested.
2. **Preserve stable data-fetch behavior** (`pykrx`, `yfinance`) unless bugfix is required.
3. **Wrap newly added high-risk logic in defensive `try/except`** to avoid breaking scans.
4. **Use English comments for newly added code blocks**.
5. **Run minimum checks before commit**:
   - `python -m compileall scanner database`
   - smoke import for changed modules
6. **DB model changes must include migration handling** for existing local DB.
7. **Notification text changes** should remain concise and avoid breaking markdown format.

---

## 5) Recommended task split between Codex and Claude Code

### Codex (best for)
- Multi-file refactors with strict constraints
- Data pipeline/logic wiring
- DB model + migration consistency

### Claude Code (best for)
- Detailed spec interpretation and prose-heavy docs
- Review of edge cases and naming/readability suggestions
- Prompt-based QA scenarios and acceptance-check lists

### Suggested handoff format
When handing off between agents, include:

```md
## Task
(what to change)

## Constraints
(no signature changes, fallback behavior, etc.)

## Files touched
(list)

## Validation run
(commands + results)

## Open risks
(any unresolved concerns)
```

---

## 6) Quick regression checklist

After scanner logic updates:

1. Run compile checks.
2. Run one KR scan and one US scan in a dry/manual mode.
3. Confirm `ScanResult` rows include `grade`.
4. Confirm notification message renders:
   - signal type
   - grade badge
   - sector line
5. Confirm no crash when external data source fails.

---

## 7) Local run commands (reference)

```bash
cd ~/Desktop/coding/myStockApp/stock-scanner
source .venv/bin/activate
python main.py
```

Optional scheduler:

```bash
python scheduler.py
```

