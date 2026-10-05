# Investment Agent Runtime — Work Handoff

## Purpose

Continue implementation of the Automaton-inspired investment agent runtime on another computer using ChatGPT Work.

The goal is **not** to replace the existing Weinstein scanner. The goal is to add an event-driven runtime layer around it:

```text
heartbeat
  -> detectors
  -> event store
  -> supervisor
  -> selected worker(s)
  -> existing scanner / portfolio / market tools
  -> review decision
  -> notification/report
```

The existing scanner remains the strategy source of truth.

---

## Repository

- GitHub repository: `mineba21/myStockApp`
- Application root: `stock-scanner/`
- Default branch: `main`
- Planning branch already created:
  - `plan/investment-agent-runtime`
- Planning branch commit containing the architecture plan:
  - `97ad0cc81d8817de35fc3ad81c5d1efeac351322`
- Plan file:
  - `stock-scanner/docs/plans/investment-agent-runtime.md`

Do **not** start from old experimental branches.

The repository instructions explicitly say that `main` is the source of truth and that old experimental branches/PRs must not be reused unless explicitly requested.

---

## First Files to Read

Before changing code, Work should read these files in this order:

1. `AGENTS.md`
2. `CLAUDE.md`
3. `docs/strategy/weinstein.md`
4. `stock-scanner/docs/plans/investment-agent-runtime.md`
5. `stock-scanner/scanner/scan_engine.py`
6. `stock-scanner/scanner/weinstein.py`
7. `stock-scanner/scanner/strict_filter.py`
8. `stock-scanner/database/models.py`
9. `stock-scanner/config.py`
10. `stock-scanner/scheduler.py`
11. `stock-scanner/web/app.py`
12. relevant tests under `stock-scanner/tests/`

---

## Current Architecture

### Existing scanner

`scanner/scan_engine.py` currently orchestrates:

- market stage loading;
- benchmark loading;
- KR universe scan;
- US universe scan;
- Weinstein analysis;
- strict filter;
- DB persistence;
- watchlist sell checks;
- Telegram notification.

### Existing scheduler

`scheduler.py` currently invokes the full scan at configured KST times.

Current default:

```text
09:00
14:00
22:00
  -> run_scan(market="ALL", triggered_by="scheduler")
```

### Existing persistence

`database/models.py` already contains:

- `ScanResult`
- `ScanLog`
- `Account`
- `Transaction`
- `Holding`
- `WatchList`

### Existing web app

`web/app.py` already exposes scanner, result, account, holding, watchlist, chart, and market APIs.

---

## Strategy Source of Truth

This is critical.

Do **not** copy Weinstein rules into a new skill/prompt file.

The strategy source of truth is:

- `docs/strategy/weinstein.md`
- `scanner/weinstein.py`
- `scanner/strict_filter.py`

Any future “skill” must be a thin adapter that invokes these sources.

Do not create a second strategy definition such as:

```text
skills/weinstein/SKILL.md
```

containing duplicated thresholds or strategy rules.

---

## Important Existing Weinstein Rules

Preserve current behavior unless a separate strategy task explicitly changes it.

Key invariants include:

- true weekly 30-week MA is the conceptual basis;
- daily 150MA is an approximation;
- Stage 2 must be above a rising 30-week MA or equivalent;
- ideal buy = Stage 1 -> Stage 2 breakout;
- breakout must come from a meaningful base/resistance;
- volume confirmation matters;
- Mansfield RS matters;
- market and sector context matter;
- strict filters are hard blocks when enabled;
- sell logic includes MA breakdown, slope deterioration, support breakdown, RS deterioration, and stop loss.

No changes to these rules are part of this handoff task.

---

# Target Runtime Architecture

```text
                 Investment Agent Runtime
                           |
                      Heartbeat
                           |
                    Event Engine
                           |
                     Supervisor
                           |
        +------------------+------------------+
        |                  |                  |
 Market Worker      Scanner Worker     Portfolio Worker
        |                  |                  |
 market_analysis     existing run_scan()    Holdings DB
        |                  |                  |
        +------------------+------------------+
                           |
                       Evidence
                           |
                    Review Decision
                           |
                WATCH / REVIEW_BUY
             REVIEW_REDUCE / REVIEW_SELL
```

---

# Allowed Initial Decisions

The supervisor is advisory only.

Allowed:

- `NO_ACTION`
- `WATCH`
- `REVIEW_BUY`
- `REVIEW_REDUCE`
- `REVIEW_SELL`

Do not implement direct order execution.

Do not produce autonomous `BUY` / `SELL` brokerage actions.

Every future trading-related decision should require human approval.

---

# Explicit Non-Goals

Do not implement any of the following in the first phase:

- brokerage order execution;
- AI self-modification of production code;
- AI self-replication;
- crypto wallets;
- USDC survival economics;
- Conway cloud dependency;
- VAA;
- LAA;
- Dual Momentum;
- All Weather;
- automatic parameter tuning;
- a new frontend framework;
- a full UI redesign;
- replacement of APScheduler;
- replacement of the existing Weinstein scanner.

---

# Next Implementation Scope — STEP A ONLY

The repository rules require one scoped implementation pass at a time.

The next Work task should implement **Step A only**.

## Add package

```text
stock-scanner/
└── agent_runtime/
    ├── __init__.py
    ├── schemas.py
    └── event_engine.py
```

Do not add heartbeat scheduling yet.

Do not modify `scheduler.py` in Step A.

Do not modify `web/app.py` in Step A.

Do not add an LLM supervisor in Step A.

---

## 1. agent_runtime/schemas.py

Use standard-library dataclasses, enums, or TypedDicts.

Avoid a new dependency unless absolutely necessary.

Define structured contracts for at least:

### Agent Event

Recommended fields:

- `event_type`
- `severity`
- `market`
- `ticker`
- `dedupe_key`
- `observed_at`
- `payload`

Initial planned event types:

- `MARKET_REGIME_CHANGED`
- `NEW_STRICT_BUY_SIGNAL`
- `SELL_SIGNAL`
- `HOLDING_DRAWDOWN`
- `HOLDING_STAGE_DETERIORATION`
- `SCAN_FAILED`
- `DATA_STALE`

### Worker Result

Recommended fields:

- `worker`
- `status`
- `observed_at`
- `evidence`
- `errors`

### Supervisor Decision

Recommended fields:

- `decision`
- `ticker`
- `market`
- `reasons`
- `worker_refs`
- `requires_human_approval`

Step A does not need to execute supervisor logic yet, but the schema can be defined.

---

## 2. agent_runtime/event_engine.py

Responsibilities:

- create an event;
- normalize or generate dedupe key;
- detect duplicates;
- enforce dedupe cooldown;
- persist events;
- mark event handled;
- query pending events;
- filter by severity/event type where useful.

Important behavior:

Repeated identical observations must not spam the DB.

Example:

```text
US market changes BULL -> CAUTION
        |
event created

10 minutes later
still CAUTION
        |
same unresolved/cooldown event
        |
do not create a duplicate
```

The dedupe logic should be deterministic and unit-testable.

---

## 3. database/models.py

Add additive models only.

Recommended models:

### AgentEvent

Table: `agent_events`

Suggested columns:

- `id`
- `created_at`
- `observed_at`
- `event_type`
- `severity`
- `market`
- `ticker`
- `dedupe_key`
- `payload_json`
- `status` — `PENDING/HANDLED/IGNORED`
- `handled_at`

Indexes should support:

- pending status lookup;
- dedupe lookup;
- event type lookup;
- ticker lookup.

### AgentWorkerRun

Table: `agent_worker_runs`

Suggested columns:

- `id`
- `started_at`
- `finished_at`
- `worker`
- `status`
- `event_id`
- `output_json`
- `error_msg`

### AgentDecision

Table: `agent_decisions`

Suggested columns:

- `id`
- `created_at`
- `event_id`
- `ticker`
- `market`
- `decision`
- `reasons_json`
- `requires_human_approval`
- `status` — `OPEN/ACKNOWLEDGED/DISMISSED`

### Migration behavior

Current repository behavior matters:

- SQLite uses the existing `_migrate()` helper.
- Non-SQLite DBs currently return early from `_migrate()`.

Do not silently invent a new production migration mechanism in this task.

Keep changes backward-compatible.

---

## 4. config.py

Add feature flags with safe defaults.

Recommended:

```python
AGENT_RUNTIME_ENABLED = false
HEARTBEAT_ENABLED = false
HEARTBEAT_INTERVAL_MINUTES = 10
EVENT_DEDUPE_MINUTES = 60
SUPERVISOR_ENABLED = false
```

Use the repository's existing environment-variable style.

Defaults must preserve current production behavior.

---

# Test Requirements for Step A

Add:

```text
stock-scanner/tests/test_agent_events.py
```

Minimum cases:

1. event creation works;
2. pending event can be queried;
3. same dedupe key inside cooldown is suppressed;
4. same event after cooldown may be created again;
5. handled event can transition from `PENDING` to `HANDLED`;
6. malformed payload does not break JSON persistence;
7. runtime-disabled path does no unexpected work if relevant;
8. DB changes do not break existing models.

Tests should not require internet access.

Use a deterministic local/test database.

---

# Validation Commands

Run from:

```bash
cd stock-scanner
```

First:

```bash
python -m compileall scanner database agent_runtime
```

Then:

```bash
pytest tests/test_agent_events.py -v
```

Regression checks:

```bash
pytest tests/test_scan_engine.py -v
pytest tests/test_strict_filter.py -v
pytest tests/test_weinstein.py -v
```

Finally:

```bash
pytest tests/ -v
```

If the environment has a project venv, prefer:

```bash
venv/bin/python -m pytest tests/ -v
```

---

# Step A Completion Criteria

Step A is complete only when all are true:

- `agent_runtime/` package exists;
- event schema is defined;
- event engine is implemented;
- additive DB models exist;
- config flags exist;
- dedupe behavior is tested;
- existing scanner tests still pass;
- Weinstein behavior is unchanged;
- scheduler behavior is unchanged;
- web/API behavior is unchanged;
- no brokerage execution path exists.

At completion, report:

- files changed;
- exact commands run;
- test results;
- schema changes;
- behavior changes;
- remaining risks;
- suggested next step.

This matches the repository's `AGENTS.md` requirements.

---

# After Step A — Do Not Automatically Continue

After Step A, stop and summarize.

The next planned phase is Step B:

```text
heartbeat.py
  -> market regime detector
  -> holding/watchlist deterioration detector
  -> stale-data detector
  -> prior scan failure detector
  -> event creation
```

Only after Step A is validated should Step B modify `scheduler.py`.

---

# Planned Step B

Future files:

```text
agent_runtime/
├── heartbeat.py
├── state.py
└── workers/
    ├── __init__.py
    ├── market_worker.py
    ├── scanner_worker.py
    └── portfolio_worker.py
```

Heartbeat must be cheaper than a full scan.

It must **not** call:

```python
run_scan(market="ALL")
```

every 10 minutes.

Full scans remain on the existing fixed schedule unless a later explicit design changes that.

---

# Planned Step C

Add a rule-based supervisor.

No LLM required initially.

Example mapping:

```text
SELL_SIGNAL + HIGH severity
    -> REVIEW_SELL

HOLDING_STAGE_DETERIORATION
    -> REVIEW_REDUCE

NEW_STRICT_BUY_SIGNAL
    -> REVIEW_BUY

no actionable event
    -> NO_ACTION
```

All decisions require human approval.

---

# Planned Long-Term Feedback Loop

The preferred “self-improvement” mechanism is measured performance, not automatic code rewriting.

For every generated signal, later track:

- +5 trading-day return;
- +20 trading-day return;
- +60 trading-day return;
- MFE;
- MAE.

Example future analytics:

```text
BREAKOUT
win rate: 68%
60d avg return: +9.4%

RE_BREAKOUT
win rate: 61%
60d avg return: +6.8%

REBOUND
win rate: 52%
60d avg return: +2.7%
```

This is the intended path toward an Investment Automaton-style feedback system.

---

# Safety / Operational Guardrails

Work must preserve these constraints:

1. No automatic brokerage orders.
2. No automatic production code mutation.
3. No automatic strategy-threshold mutation.
4. No duplicate Weinstein rule source.
5. No secret/environment-variable logging.
6. No disabling current strict gates.
7. No removal of current fixed scans in Step A.
8. No unrelated refactor.
9. No look-ahead bias.
10. No destructive DB schema change.

---

# Recommended Work Prompt

Use the following as the first prompt in Work after opening the repository:

> Open `mineba21/myStockApp` and continue from branch `plan/investment-agent-runtime`.
> Read `AGENTS.md`, `CLAUDE.md`, `docs/strategy/weinstein.md`, and `stock-scanner/docs/plans/investment-agent-runtime.md` first.
> Then implement **Step A only** from the handoff: `agent_runtime/schemas.py`, `agent_runtime/event_engine.py`, additive agent DB models/migration support, config feature flags, and `tests/test_agent_events.py`.
> Do not modify Weinstein strategy behavior, `scheduler.py`, or `web/app.py`.
> Run the specified compile and pytest regression checks.
> Stop after Step A and report files changed, commands run, test results, schema changes, remaining risks, and next step.

---

## Final State at Handoff

No runtime production code has been implemented yet.

Completed so far:

- repository inspected;
- current architecture mapped;
- Automaton concepts filtered for relevance;
- planning branch created;
- architecture plan committed.

Planning branch:

```text
plan/investment-agent-runtime
```

Plan commit:

```text
97ad0cc81d8817de35fc3ad81c5d1efeac351322
```

The next computer/Work session should start from this branch and implement Step A only.
