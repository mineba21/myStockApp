# Investment Agent Runtime Plan

## Current State Summary

The runnable application lives under `stock-scanner/`.

Current orchestration is centered on `scanner/scan_engine.py`:

- `run_scan()` loads market state and benchmarks.
- It scans KR/US universes.
- It applies the existing Weinstein strategy and strict filter.
- It persists scan results.
- It checks the sell watchlist.
- It sends Telegram notifications.
- `scheduler.py` invokes the full scan at fixed KST times.
- `database/models.py` persists scan results, scan logs, accounts, transactions, holdings, and watchlist data.
- `web/app.py` exposes scan/status/results/account/holding/watchlist APIs and starts the scheduler.

The strategy source of truth remains:

- `docs/strategy/weinstein.md`
- `scanner/weinstein.py`
- `scanner/strict_filter.py`

This plan does **not** duplicate Weinstein rules in a new skill document.

## Goal

Add an Automaton-inspired runtime layer around the existing scanner without replacing or weakening the current strategy engine.

Target flow:

```text
heartbeat
  -> detectors
  -> event store
  -> supervisor
  -> selected worker(s)
  -> existing scanner / portfolio / market tools
  -> decision artifact
  -> notification/report
```

The runtime should make scheduled work event-driven, observable, and extensible while keeping existing manual and scheduled scans backward-compatible.

## Non-Goals

This phase will not:

- change Weinstein strategy rules;
- change strict filter semantics;
- enable automatic brokerage orders;
- allow AI self-modification of production code;
- allow agent self-replication;
- add wallet/USDC/survival economics;
- add VAA/LAA/Dual Momentum logic yet;
- replace existing FastAPI UI;
- remove existing APScheduler fixed schedules.

## Design Principles

1. Existing scanner remains the source of truth for stock signals.
2. Runtime orchestration is additive and feature-flagged.
3. Workers return structured evidence; they do not place trades.
4. Supervisor produces recommendations only.
5. Existing strategy docs are referenced, not copied.
6. Every heartbeat/event/worker run is auditable.
7. Failures degrade gracefully and must not block the existing scheduled scan path.
8. No look-ahead data or future leakage may be introduced.
9. Manual `run_scan()` behavior stays backward-compatible.

## Proposed Package Layout

```text
stock-scanner/
├── agent_runtime/
│   ├── __init__.py
│   ├── heartbeat.py
│   ├── event_engine.py
│   ├── supervisor.py
│   ├── schemas.py
│   ├── state.py
│   └── workers/
│       ├── __init__.py
│       ├── market_worker.py
│       ├── scanner_worker.py
│       └── portfolio_worker.py
├── scanner/
│   └── ... existing files unchanged where possible
├── database/
│   └── models.py
├── scheduler.py
├── config.py
├── web/
│   └── app.py
└── tests/
    ├── test_agent_events.py
    ├── test_heartbeat.py
    ├── test_supervisor.py
    └── existing tests...
```

## Runtime Contracts

### Event

Structured event payload:

```python
{
    "event_type": "MARKET_REGIME_CHANGED",
    "market": "US",
    "ticker": None,
    "severity": "HIGH",
    "dedupe_key": "market:US:regime",
    "observed_at": "...",
    "payload": {...}
}
```

Initial event types:

- `MARKET_REGIME_CHANGED`
- `NEW_STRICT_BUY_SIGNAL`
- `SELL_SIGNAL`
- `HOLDING_DRAWDOWN`
- `HOLDING_STAGE_DETERIORATION`
- `SCAN_FAILED`
- `DATA_STALE`

### Worker Result

```python
{
    "worker": "scanner",
    "status": "OK",
    "observed_at": "...",
    "evidence": {...},
    "errors": []
}
```

### Supervisor Decision

```python
{
    "decision": "WATCH",
    "ticker": "VRSN",
    "market": "US",
    "reasons": [...],
    "worker_refs": [...],
    "requires_human_approval": True
}
```

Allowed initial decisions:

- `NO_ACTION`
- `WATCH`
- `REVIEW_BUY`
- `REVIEW_REDUCE`
- `REVIEW_SELL`

No direct `BUY` or `SELL` execution action is allowed in this phase.

## Phase 1 Scope

Phase 1 adds only the runtime foundation.

### Files to Add

#### `agent_runtime/schemas.py`

- lightweight dataclasses or typed dicts for events, worker results, and decisions;
- no new dependency required.

#### `agent_runtime/event_engine.py`

Responsibilities:

- create events;
- compute dedupe keys;
- suppress duplicate unresolved events within a configurable cooldown;
- mark events handled;
- query pending events by severity/type.

#### `agent_runtime/state.py`

Responsibilities:

- runtime state access helpers;
- last heartbeat time;
- detector checkpoints;
- worker run metadata.

#### `agent_runtime/heartbeat.py`

Responsibilities:

- run low-cost detectors;
- persist only actionable events;
- not call the full stock universe scan every tick.

Initial detectors:

1. market regime change using existing `scanner.market_analysis.get_market_stages()`;
2. watchlist/holding deterioration using existing watchlist/holding data and existing scanner functions where possible;
3. stale-data detection;
4. prior scan failure detection.

#### `agent_runtime/workers/scanner_worker.py`

Adapter around existing `scanner.scan_engine.run_scan()`.

It must not reimplement Weinstein rules.

#### `agent_runtime/workers/market_worker.py`

Adapter around existing market-stage functions.

#### `agent_runtime/workers/portfolio_worker.py`

Read-only summary of current holdings/account exposure from DB.

#### `agent_runtime/supervisor.py`

Rule-based supervisor first.

No LLM dependency in Phase 1.

It consumes pending events and worker evidence and emits review decisions.

### Files to Change

#### `database/models.py`

Add additive tables:

- `agent_events`
- `agent_worker_runs`
- `agent_decisions`
- optional `agent_state`

Recommended minimal schemas:

`agent_events`

- id
- created_at
- observed_at
- event_type
- severity
- market
- ticker
- dedupe_key
- payload_json
- status (`PENDING/HANDLED/IGNORED`)
- handled_at

`agent_worker_runs`

- id
- started_at
- finished_at
- worker
- status
- event_id
- output_json
- error_msg

`agent_decisions`

- id
- created_at
- event_id
- ticker
- market
- decision
- reasons_json
- requires_human_approval
- status (`OPEN/ACKNOWLEDGED/DISMISSED`)

All schema changes must be additive and backward-compatible.

SQLite migration logic must be extended consistently.

For non-SQLite databases, no implicit `ALTER TABLE` behavior should be added beyond the repository's existing policy.

#### `config.py`

Add feature flags and intervals:

- `AGENT_RUNTIME_ENABLED=false`
- `HEARTBEAT_ENABLED=false`
- `HEARTBEAT_INTERVAL_MINUTES=10`
- `EVENT_DEDUPE_MINUTES=60`
- `SUPERVISOR_ENABLED=false`

Defaults must preserve current behavior.

#### `scheduler.py`

Keep existing fixed scan jobs.

Add a separate heartbeat job only when the feature flag is enabled.

The heartbeat job must use `max_instances=1`.

A heartbeat failure must be logged but must not stop fixed scan jobs.

#### `web/app.py`

Phase 1 adds read-only endpoints only:

- `GET /api/agent/events`
- `GET /api/agent/decisions`
- `GET /api/agent/status`

No UI redesign in Phase 1.

## Heartbeat Behavior

Heartbeat is deliberately cheaper than a full scan.

Pseudo-flow:

```text
tick
  -> load last state
  -> market detector
  -> holding/watchlist detector
  -> stale-data detector
  -> scan-health detector
  -> create deduplicated events
  -> if actionable event exists and supervisor enabled:
       supervisor handles pending events
  -> persist heartbeat state
```

The heartbeat must not call `run_scan(market="ALL")` on every tick.

A full scanner run may be requested only by explicit event handling logic or existing fixed schedules.

## Compatibility With Existing Scheduler

Existing:

- configured `SCHEDULE_TIMES` call `run_scan(market="ALL", triggered_by="scheduler")`.

New:

- heartbeat schedule runs independently.

This allows gradual adoption:

1. deploy runtime disabled;
2. enable heartbeat logging only;
3. enable event creation;
4. enable supervisor;
5. later decide whether any fixed scans can be reduced.

## Strategy Skill Policy

Do not create a duplicate `skills/weinstein/SKILL.md` containing strategy rules.

If a future skill layer is added, it should only reference:

- `docs/strategy/weinstein.md`;
- `scanner/weinstein.py`;
- `scanner/strict_filter.py`.

Example responsibility of a future Weinstein skill adapter:

- describe when to invoke the scanner worker;
- validate required inputs;
- return structured evidence.

It must not become another source of truth for threshold values or strategy invariants.

## Observability

Phase 1 should make these measurable:

- heartbeat runs;
- events created;
- duplicate events suppressed;
- worker runs;
- worker failures;
- decisions created;
- unresolved decisions;
- scan failures.

Structured logs should include:

- run_id
- event_id
- worker
- ticker
- market
- duration_ms
- status

No secrets or credentials may be logged.

## Testing Plan

### New Unit Tests

`tests/test_agent_events.py`

- event creation;
- dedupe behavior;
- cooldown expiry;
- status transition.

`tests/test_heartbeat.py`

- regime change generates event;
- unchanged regime does not create duplicate event;
- failed detector does not crash whole heartbeat;
- runtime disabled performs no work.

`tests/test_supervisor.py`

- high-severity sell deterioration -> `REVIEW_SELL`;
- strict buy event -> `REVIEW_BUY`;
- no actionable events -> `NO_ACTION`;
- all decisions require human approval.

### Regression Tests

Run all existing scanner tests to verify:

- Weinstein calculations unchanged;
- strict filter unchanged;
- chart/results APIs unchanged;
- scan persistence unchanged.

## Verification Commands

From `stock-scanner/`:

```bash
python -m compileall scanner database agent_runtime
pytest tests/test_agent_events.py -v
pytest tests/test_heartbeat.py -v
pytest tests/test_supervisor.py -v
pytest tests/test_scan_engine.py -v
pytest tests/test_strict_filter.py -v
pytest tests/test_weinstein.py -v
pytest tests/ -v
```

If the web endpoints are changed:

```bash
python main.py
```

Manual checks:

- existing `/api/scan/start` still works;
- `/api/scan/status` still reports fixed schedules;
- runtime endpoints are read-only;
- disabling runtime returns the app to current behavior.

## Rollout Plan

### Step A — Runtime foundation

Implement schemas, DB tables, event engine, feature flags, tests.

No heartbeat scheduling yet.

### Step B — Heartbeat

Add detectors and scheduler integration.

Keep supervisor disabled.

### Step C — Supervisor

Enable rule-based decisions only.

No LLM and no order execution.

### Step D — Worker expansion

Add ETF/VAA/LAA/Dual Momentum workers only after their own strategy sources of truth are defined.

### Step E — Performance feedback

Add post-signal outcome tracking:

- +5d
- +20d
- +60d
- MFE
- MAE

This is the preferred "self-improvement" mechanism.

It measures strategy quality instead of allowing the AI to rewrite production strategy code automatically.

## Risks

### Duplicate orchestration

Risk: heartbeat and fixed scheduler trigger the same full work.

Mitigation:

- heartbeat does not call full scan by default;
- event dedupe;
- existing `scan_status["is_running"]` remains respected.

### DB schema drift

Risk: SQLite and non-SQLite behavior diverge.

Mitigation:

- additive models;
- explicit migration tests;
- do not silently invent non-SQLite migration behavior.

### Strategy duplication

Risk: worker/skill code copies Weinstein rules.

Mitigation:

- adapters only;
- tests ensure decisions consume scanner outputs rather than recomputing thresholds.

### Alert fatigue

Risk: repeated events create noise.

Mitigation:

- dedupe keys;
- cooldown;
- severity;
- unresolved-event suppression.

### Premature autonomous trading

Risk: recommendation layer becomes execution layer.

Mitigation:

- Phase 1 decisions are review-only;
- all decisions store `requires_human_approval=True`;
- no brokerage order API in runtime package.

## Rollback

Runtime is controlled by feature flags.

Rollback path:

1. disable `AGENT_RUNTIME_ENABLED`;
2. disable `HEARTBEAT_ENABLED`;
3. existing fixed scheduler and manual scan paths continue unchanged;
4. additive DB tables can remain without affecting legacy behavior.

## Suggested First Implementation Pass

Implement **Step A only** in the next scoped task:

- add `agent_runtime/schemas.py`;
- add `agent_runtime/event_engine.py`;
- add additive DB models;
- add config feature flags;
- add event-engine tests;
- do not modify `scheduler.py` or `web/app.py` yet.

This keeps the first implementation pass under control and aligned with the repository rule requiring one scoped implementation phase at a time.
