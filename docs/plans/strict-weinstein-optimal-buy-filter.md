# Strict Weinstein Optimal Buy Filter — 구현 계획

> 최종 위치: `docs/plans/strict-weinstein-optimal-buy-filter.md` (구현 단계 첫 PR에서 이 파일로 복사·커밋)

---

## Context

이전 작업(PR #3, 머지 완료)으로 v4 base pivot · Mansfield RS · REBOUND 주봉 게이트 · DataFetchError가 신호 경로에 연결되었다. 그러나 CLAUDE.md가 정의한 **strict Weinstein optimal buy filter**의 8개 mandatory gate 중 절반 가까이가 여전히 *컴퓨트만 되고 차단은 안 하는* 상태다 — Mansfield RS는 `rs_passed` 플래그를 만들어두지만 누구도 읽지 않고, 주봉 30-SMA는 stage 분류용일 뿐 hard block이 아니며, sector·stop-loss·zero-cross는 아예 부재. `STRICT_*` 플래그 14개도 전부 미정의.

이 계획의 목표는 스캐너를 "후보 발견기"에서 "엄격한 최적 매수 필터"로 전환하는 것이다. 거부 사유는 `filter_reasons`로 추적되어야 하며, 실패한 mandatory gate는 절대 `warning_flags`로 강등되어선 안 된다(CLAUDE.md:60–66, 156–168).

### 사용자 결정 (2026-04-29)

1. **Sector gate 범위**: 이번 계획은 **DB 컬럼 + 플래그 스텁**까지만. 종목당 sector→ETF 매핑은 후속 plan(`strict-weinstein-sector-mapping.md`)으로 분리. `STRICT_REQUIRE_SECTOR_STAGE2` 기본 False로 도입.
2. **`STRICT_WEINSTEIN_MODE` 첫 릴리스 기본값**: **True**. CLAUDE.md 권장(:212)을 그대로 준수 — 시그널이 급감하더라도 strategy invariant 우선.
3. **거부 시그널 저장 정책**: `STRICT_PERSIST_REJECTED`(기본 False) 디버그 플래그가 켜졌을 때만 strict 거부 시그널을 DB에 기록. 평소 DB는 strict-pass만 보유. 알림은 모든 모드에서 strict-pass만 발송.

---

## Current-State Summary

| 게이트 | 현재 상태 | 근거 (file:line) |
|---|---|---|
| 1. Market | ✅ BEAR full block, CAUTION 토글 | `scan_engine.py:21–48` `_get_market_filter_decision` |
| 2. Sector | ❌ 6 US ETF + 3 KR ETF 시장-광역 통계만 존재, 종목당 매핑 없음 | `market_analysis.py:23–66` |
| 3. Weekly Stage | ⚠️ BEAR+STAGE4만 차단; 주봉 close ≥ 30w-SMA가 hard block 아님 | `weinstein.py:144–179, 941–942` |
| 4. Base/Pivot | ✅ 5wk min, ≤15% width, look-ahead 없음 | `weinstein.py:239–298, 309–393` |
| 5. Volume | ✅ 일봉 ≥3.0x / 주봉 ≥2.0x hard block | `weinstein.py:330, 366` |
| 6. Mansfield RS | ⚠️ `rs_value`/`rs_trend` 계산되지만 gating 없음, zero-cross 미구현 | `weinstein.py:182–236, 1004` |
| 7. Extension | ✅ MA150 +15% hard block | `weinstein.py:362` |
| 8. Stop-loss | ❌ BUY signal dict에 부재, DB에도 없음 | `weinstein.py:980–1011`; SELL만 `check_sell_signal:1075` |

**부재 인프라**: `STRICT_*` 플래그 14개 전부 / DB 컬럼 7개(`stop_loss`, `sector_name`, `sector_stage`, `rs_trend`, `rs_zero_crossed`, `strict_filter_passed`, `filter_reasons`) / `apply_strict_filter` 같은 strict-pass 결정 계층 / RS zero-cross 감지 / stop-loss 계산 헬퍼.

---

## Goals

1. **CLAUDE.md 8 게이트 모두**가 strict 모드에서 hard-block. 실패 사유는 `filter_reasons`에 기록되어 회귀 추적 가능.
2. `STRICT_WEINSTEIN_MODE=True`(기본) 일 때 strict-pass 시그널만 저장·알림.
3. `STRICT_PERSIST_REJECTED=True`로 토글 시 거부 시그널도 DB에 영속화 → 백테스트/QA용 데이터 확보.
4. CLAUDE.md 17개 테스트 카테고리 전부 합성 픽스처로 통과.
5. `weinstein.py`는 외부 데이터 호출 없이 합성 pandas로 테스트 가능 상태 유지(CLAUDE.md:247–249).

## Non-Goals

- **종목당 sector 매핑** (yfinance ticker.info, KRX 업종 분류). 별도 plan.
- **백테스팅 프레임워크** 신규 구축. 기존 DB 영속화로 사후 분석 가능하면 충분.
- **알림 채널 다변화** (이메일·Discord 등). Telegram 만 유지.
- **웹 UI에 strict 토글 노출**. 환경변수만으로 제어.
- **alembic 도입**. 현행 `_migrate()` 패턴(PRAGMA + ALTER TABLE) 그대로 활용.
- **legacy 신호 dict 키 제거** (`rs`, `stage` 등). 한 릴리스 동안 공존, 후속 정리 plan에서 처리.

---

## Files to Change

각 phase는 ≤4 파일 + 단일 테마(CLAUDE.md broad-changes 규칙).

| File | Phase 1 | Phase 2 | Phase 3 | Phase 4 |
|---|:-:|:-:|:-:|:-:|
| `stock-scanner/config.py` | ✱ 14개 STRICT_* 플래그 | | | |
| `stock-scanner/database/models.py` | ✱ 7개 컬럼 + `_migrate()` 확장 | | | |
| `stock-scanner/scanner/weinstein.py` | ✱ 신호 dict 스캐폴드(stop_loss/strict_filter_passed=None) | ✱ stop-loss 계산 + RS zero-cross | ✱ 주봉/일봉 MA hard gate를 진단 시그널에 포함 | |
| `stock-scanner/scanner/scan_engine.py` | | ✱ apply_strict_filter 스켈레톤 | ✱ market·stage·extension gate 연동 | ✱ STRICT_PERSIST_REJECTED 분기 + notify 가드 |
| `stock-scanner/scanner/strict_filter.py` (신규) | | | ✱ 게이트 결정 함수 | |
| `stock-scanner/notifications/telegram.py` | | | | ✱ filter_reasons 표시(opt-in) |
| `stock-scanner/tests/test_weinstein.py` | ✱ scaffold 테스트 | ✱ stop-loss/zero-cross 단위 테스트 | | |
| `stock-scanner/tests/test_strict_filter.py` (신규) | | ✱ 단위 테스트 | ✱ 통합 테스트 | |
| `stock-scanner/tests/test_scan_engine.py` | | | | ✱ persist/notify 분기 |
| `docs/plans/strict-weinstein-optimal-buy-filter.md` | ✱ 이 문서 복사 | | | |
| `docs/strategy/weinstein.md` | | | | ✱ 거래량 hard block 표현 정정 |

---

## Strict Weinstein Gates (구현 정의)

각 게이트는 `apply_strict_filter(signal, ctx) -> (passed: bool, reasons: list[str])`에서 평가. 실패 사유 문자열은 안정적인 enum 형식(`"market_bear"`, `"rs_below_zero"` 등)으로 통일.

### Gate 1 — Market

```
input:  ctx.market_condition ∈ {BULL, NEUTRAL, CAUTION, BEAR, UNKNOWN}
fail when:
  - market_condition == "BEAR"                                          → "market_bear"
  - market_condition == "UNKNOWN" and STRICT_REQUIRE_MARKET_CONFIRMATION → "market_unknown"
  - market_condition == "CAUTION" and signal_type in {BREAKOUT, RE_BREAKOUT}
    and STRICT_BLOCK_CAUTION_BREAKOUTS                                  → "market_caution_breakout"
```

### Gate 2 — Sector (스텁)

```
input:  ctx.sector_stage ∈ {STAGE1..STAGE4, UNKNOWN, None}
fail when:
  - STRICT_REQUIRE_SECTOR_STAGE2 and sector_stage in {STAGE4}           → "sector_stage4"
  - STRICT_REQUIRE_SECTOR_STAGE2 and sector_stage != "STAGE2"           → "sector_not_stage2"
note: 본 plan에서는 sector_stage가 항상 None으로 들어옴 → STRICT_REQUIRE_SECTOR_STAGE2=False 기본값에서는 통과.
      후속 plan에서 sector 매핑 구현 시 그대로 활성화.
```

### Gate 3 — Stock Weekly Stage

```
input:  weekly_ind, daily_ind, signal.stage_v4
fail when:
  - weekly_ind is None                                                  → "weekly_data_missing"
  - close < cur_sma30w (STRICT_REQUIRE_PRICE_ABOVE_WEEKLY_30MA)         → "below_weekly_30ma"
  - signal_type == BREAKOUT and price < daily ma150
    and STRICT_REQUIRE_PRICE_ABOVE_DAILY_150MA                          → "below_daily_150ma"
  - stage_v4 in {STAGE3, STAGE4}                                        → f"stage_{stage_v4.lower()}"
  - stage_v4 == STAGE2 and slope30w <= 0                                → "weekly_30ma_slope_negative"
```

### Gate 4 — Base / Pivot

`detect_stage2_breakout`이 이미 hard-block. strict filter는 signal에 `pivot_price` / `base_weeks` / `base_quality_v4`가 있는지 검증만:

```
fail when:
  - signal_type == BREAKOUT and (pivot_price is None or base_weeks < BASE_MIN_WEEKS) → "base_insufficient"
  - signal_type == BREAKOUT and base_quality_v4 == "WIDE"               → "base_too_wide"
  - signal_type == REBOUND and v4_gate not in {BASE_RETEST, 30W_RETEST} → "rebound_no_retest"
```

### Gate 5 — Breakout Volume

이미 detect 단에서 hard-block. strict filter는 sanity 검증:

```
fail when (BREAKOUT only, STRICT_REQUIRE_BREAKOUT_VOLUME):
  - vol_ratio < BREAKOUT_DAILY_VOL_RATIO                                → "breakout_daily_volume"
  - weekly_volume_ratio < BREAKOUT_WEEKLY_VOL_RATIO                     → "breakout_weekly_volume"
```

### Gate 6 — Mansfield RS

```
input:  rs_value, rs_trend, rs_zero_crossed (Phase 2 신규)
fail when:
  - benchmark_close is None and STRICT_REQUIRE_RS_POSITIVE              → "rs_benchmark_missing"
  - rs_value < 0.0    and STRICT_REQUIRE_RS_POSITIVE                    → "rs_below_zero"
  - rs_trend == "FALLING" and STRICT_REQUIRE_RS_RISING                  → "rs_falling"
  - signal_type == BREAKOUT and not rs_zero_crossed
    and STRICT_REQUIRE_RS_ZERO_CROSS_FOR_BREAKOUT                       → "rs_no_zero_cross"
```

`rs_zero_crossed`는 Phase 2 신규 헬퍼: 최근 `RS_ZERO_CROSS_LOOKBACK_WEEKS`(8) 안에 `rs_value`가 음수→양수로 한 번이라도 전환했는지를 주봉 시리즈에서 판정. `compute_relative_performance`를 시계열 반환으로 확장하거나 별도 `detect_rs_zero_cross(close, benchmark_close, lookback_weeks)` 헬퍼 추가.

### Gate 7 — Extension / Risk

```
fail when:
  - (price - ma150) / ma150 * 100 > BREAKOUT_MAX_EXTENDED_PCT           → "extended_above_ma150"
  - signal_type == BREAKOUT and (price - sma30w) / sma30w * 100 > 30    → "extended_above_30w"
```

### Gate 8 — Stop-Loss

```
input:  signal.stop_loss
fail when:
  - stop_loss is None and STRICT_REQUIRE_STOP_LOSS                      → "stop_loss_missing"
  - stop_loss >= price (sanity, 항상 활성)                              → "stop_loss_above_price"
```

`compute_stop_loss(signal, df, daily_ind, weekly_ind) -> Optional[float]` 헬퍼는 Phase 2 신규:

| signal_type | stop-loss 후보 (우선순위 순) |
|---|---|
| `BREAKOUT` | `base_low * 0.99` → `pivot_price * 0.97` → `cur_sma30w * 0.97` |
| `RE_BREAKOUT` | `recent_swing_low * 0.99` (직전 30일 최저) → `cur_m50 * 0.97` |
| `REBOUND` | `cur_sma30w * 0.97` → `cur_m50 * 0.97` |

모든 후보가 `>= price`이면 None 반환.

---

## Design Decisions

### D1. 신규 모듈: `scanner/strict_filter.py`

`weinstein.py`와 `scan_engine.py` 사이의 결정 계층. 외부 데이터 호출 없이 signal dict + ctx만 받아 `(passed, reasons)` 반환. `weinstein.py` 순수성 유지(CLAUDE.md:247–249).

```python
# scanner/strict_filter.py
from typing import Tuple, List
from config import (...)

GateContext = TypedDict("GateContext", {
    "market_condition": Optional[str],
    "sector_stage":     Optional[str],
    "benchmark_present": bool,
})

def apply_strict_filter(signal: dict, ctx: GateContext) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    if not STRICT_WEINSTEIN_MODE:
        return True, reasons
    _check_market(signal, ctx, reasons)
    _check_sector(signal, ctx, reasons)
    _check_weekly_stage(signal, reasons)
    _check_base(signal, reasons)
    _check_volume(signal, reasons)
    _check_rs(signal, ctx, reasons)
    _check_extension(signal, reasons)
    _check_stop_loss(signal, reasons)
    return (len(reasons) == 0), reasons
```

각 `_check_*`는 신호 dict의 컬럼만 읽어 reason을 append. 단위 테스트가 게이트별로 가능.

### D2. `analyze_stock` 출력 확장

진단 시그널은 항상 반환(`STRICT_*`로 거르지 않음). 신규 키:

- `stop_loss: Optional[float]` — Phase 2.
- `rs_zero_crossed: Optional[bool]` — Phase 2.
- `weekly_stage: str` — 이미 존재(`v4_stage`로). 키 명명 통일.
- `strict_filter_passed: Optional[bool]` — Phase 1에서 None 스캐폴드, scan_engine이 `apply_strict_filter` 후 채움.
- `filter_reasons: List[str]` — Phase 1에서 빈 리스트 스캐폴드, scan_engine이 채움.

### D3. scan_engine 흐름

```python
res = analyze_stock(...)
if res is None:
    continue
ctx = {"market_condition": market_condition,
       "sector_stage":     None,                        # Phase 5에서 채움
       "benchmark_present": benchmark_close is not None}
passed, reasons = apply_strict_filter(res, ctx)
res["strict_filter_passed"] = passed
res["filter_reasons"]       = reasons

if passed:
    _save(db, res); buy_signals.append(res)
elif STRICT_PERSIST_REJECTED:
    _save(db, res)                                      # debug-only persistence
# 아니면 drop
```

`_get_market_filter_decision`은 그대로 둔다 — Gate 1과 중복되지만 BEAR 신호는 `analyze_stock` 진입 자체를 막아 비용을 절약하는 fast-path. apply_strict_filter는 그래도 sanity check로 동일 조건을 평가(중복은 cheap).

### D4. RS zero-cross 감지

`compute_relative_performance`를 `(rs_value, rs_trend, rs_series)`로 확장하지 않고, 별도 헬퍼:

```python
def detect_rs_zero_cross(close: pd.Series,
                         benchmark_close: pd.Series,
                         lookback_weeks: int = RS_ZERO_CROSS_LOOKBACK_WEEKS) -> bool:
    """최근 lookback_weeks 안에 Mansfield RS 가 음수→양수 전환했는지."""
    ...
```

순수 함수, 합성 데이터 단위 테스트 가능.

### D5. DB 영속화

`_save()`에 7개 신규 컬럼 추가. `_migrate()`(`models.py:185–218`)에 ALTER TABLE 라인 7개 추가. 기존 DB 호환:

- `stop_loss FLOAT` (NULL)
- `sector_name VARCHAR(50)` (NULL)
- `sector_stage VARCHAR(10)` (NULL)
- `rs_trend VARCHAR(10)` (NULL)
- `rs_zero_crossed BOOLEAN` (NULL)
- `strict_filter_passed BOOLEAN` (NULL)
- `filter_reasons TEXT` (NULL, JSON 문자열)

`filter_reasons`는 `json.dumps(reasons)` 문자열 저장 → 가독성보다 forward-compat.

### D6. 알림 가드

`scan_engine._notify()`는 인자로 `[res]` 리스트만 받음 — 상위가 이미 strict-pass로 거른 후이므로 알림 코드 수정 최소. `filter_reasons` 표기는 별도 옵션(`STRICT_NOTIFY_INCLUDE_REASONS=False` 기본)으로 추가하지 않음 — 메시지 길이/노이즈 방지.

### D7. legacy `_get_market_filter_decision` 정합성

| 조건 | legacy 결과 | strict_filter 결과 | 정합 |
|---|---|---|---|
| BEAR | block | block | ✓ |
| CAUTION + BREAKOUT (block_breakout) | block | block | ✓ |
| CAUTION + BREAKOUT (allow_with_flag) | allow + flag | block (STRICT_BLOCK_CAUTION_BREAKOUTS=True) | ⚠ legacy가 더 관대 |

→ STRICT 모드에서 `CAUTION_MODE`는 무시되고 `STRICT_BLOCK_CAUTION_BREAKOUTS`가 우선. 문서화 필요.

---

## DB / Schema Impact

### 신규 컬럼 (모두 NULL 허용)

```sql
ALTER TABLE scan_results ADD COLUMN stop_loss            FLOAT;
ALTER TABLE scan_results ADD COLUMN sector_name          VARCHAR(50);
ALTER TABLE scan_results ADD COLUMN sector_stage         VARCHAR(10);
ALTER TABLE scan_results ADD COLUMN rs_trend             VARCHAR(10);
ALTER TABLE scan_results ADD COLUMN rs_zero_crossed      BOOLEAN;
ALTER TABLE scan_results ADD COLUMN strict_filter_passed BOOLEAN;
ALTER TABLE scan_results ADD COLUMN filter_reasons       TEXT;
```

### `models.py` 변경 지점

- `ScanResult` 클래스에 7개 컬럼 선언 추가.
- `_migrate()` 의 `cols_to_add` (lines 206–218)에 7개 (col, ddl) 튜플 추가.
- 인덱스: `strict_filter_passed`에 단일 인덱스(자주 필터 조건). 나머지는 인덱스 없음.

### `_save()` 변경 (`scan_engine.py:260–308`)

- INSERT/UPDATE 양쪽에 신규 컬럼 7개 매핑 추가.
- `filter_reasons`는 `json.dumps(reasons or [])`.

### 마이그레이션 안전성

기존 DB 파일은 `_migrate()`가 자동 ALTER. 컬럼이 이미 존재하면 PRAGMA 검사로 skip. 신규 클론은 `Base.metadata.create_all` 한 번에 처리.

### 후방 호환

기존 `rs_value` 컬럼 의미는 Mansfield RS 그대로 유지. `rs_trend` 신규 컬럼 추가로 RS 정보 분할 영속화.

---

## Test Plan

### Phase 1 — 인프라 (no-op 동작)

`tests/test_weinstein.py`:
- `analyze_stock` 결과 dict에 `stop_loss=None`, `strict_filter_passed=None`, `filter_reasons=[]` 키가 존재하는지(동작 변화는 없으나 후속 phase 사전 검증).

`tests/test_scan_engine.py::TestSavePersistsStrictFields`(신규, 3 tests):
- `_save()`가 신규 7개 컬럼을 NULL로 정상 저장.
- 기존 `stock_scanner.db`(누락 컬럼 가정) 위에서 `_migrate()` 실행 후 컬럼이 추가됨(임시 SQLite + 빈 테이블 생성 후 ALTER 시뮬레이션).
- `_save()`가 `rs_trend="RISING"` 등 신규 값 영속화.

### Phase 2 — Stop-loss + RS zero-cross + RS hard gates

`tests/test_weinstein.py::TestStopLoss`(신규, 5 tests):
- BREAKOUT signal에서 `compute_stop_loss`가 `base_low * 0.99` 반환.
- REBOUND signal에서 `cur_sma30w * 0.97` 반환.
- 모든 후보가 `>= price`면 None.
- price=stop 같으면 None.
- analyze_stock 출력에 stop_loss 포함.

`tests/test_weinstein.py::TestRSZeroCross`(신규, 4 tests):
- 종목 RS가 8주 안에 음→양 전환 → True.
- 줄곧 음수 → False.
- 줄곧 양수(전환 없음) → False.
- benchmark None → False.

`tests/test_strict_filter.py::TestRSGate`(신규, 6 tests):
- `rs_value=-1.0` + STRICT_REQUIRE_RS_POSITIVE=True → fail("rs_below_zero").
- `rs_trend="FALLING"` + STRICT_REQUIRE_RS_RISING=True → fail("rs_falling").
- `benchmark_present=False` + STRICT_REQUIRE_RS_POSITIVE=True → fail("rs_benchmark_missing").
- BREAKOUT + `rs_zero_crossed=False` + STRICT_REQUIRE_RS_ZERO_CROSS_FOR_BREAKOUT=True → fail.
- REBOUND은 zero-cross 요구 안 함.
- 모든 RS 게이트 통과 케이스.

### Phase 3 — Stage / Extension / Volume / Base 통합

`tests/test_strict_filter.py::TestStageGate` / `TestExtensionGate` / `TestVolumeGate` / `TestBaseGate`:
- 각 게이트별 fail/pass 케이스 (CLAUDE.md:298–320 17 카테고리 모두 커버).
- 합산 케이스: 여러 게이트 동시 실패 시 모든 reason 누적.

### Phase 4 — End-to-end

`tests/test_scan_engine.py::TestStrictFilterFlow`(신규, 5 tests):
- STRICT_WEINSTEIN_MODE=True + 모든 게이트 통과 → `_save` 호출, notify 리스트에 포함.
- STRICT_WEINSTEIN_MODE=True + RS 음수 → `_save` 미호출(STRICT_PERSIST_REJECTED=False), notify 미포함.
- STRICT_WEINSTEIN_MODE=True + STRICT_PERSIST_REJECTED=True + RS 음수 → `_save` 호출(strict_filter_passed=False), notify 미포함.
- STRICT_WEINSTEIN_MODE=False → 모든 시그널 저장·알림(legacy 호환).
- 거부 시그널의 `filter_reasons` JSON이 DB에 정상 직렬화되어 저장됨.

### CLAUDE.md 17개 카테고리 매핑

| Cat. | Phase | Test |
|---|---|---|
| valid strict BREAKOUT | 4 | `TestStrictFilterFlow::test_all_gates_pass` |
| negative Mansfield RS | 2 | `TestRSGate::test_rs_below_zero_blocks` |
| falling Mansfield RS | 2 | `TestRSGate::test_rs_falling_blocks` |
| missing benchmark | 2 | `TestRSGate::test_no_benchmark_blocks` |
| no recent RS zero-cross | 2 | `TestRSGate::test_no_zero_cross_blocks_breakout` |
| price below weekly 30-SMA | 3 | `TestStageGate::test_below_weekly_30ma` |
| price below daily MA150 | 3 | `TestStageGate::test_below_daily_150ma_breakout` |
| Stage 3 / Stage 4 | 3 | `TestStageGate::test_stage3_blocks` / `test_stage4_blocks` |
| missing/unknown sector | 3 | `TestSectorGate::test_unknown_sector_blocks_when_required` |
| sector Stage 4 | 3 | `TestSectorGate::test_sector_stage4_blocks` |
| insufficient breakout volume | 3 | `TestVolumeGate::test_low_breakout_volume` |
| invalid/wide base | 3 | `TestBaseGate::test_wide_base_blocks` (기존 V4 + sanity) |
| overextended breakout | 3 | `TestExtensionGate::test_extended_blocks` |
| strict BUY includes stop-loss | 4 | `TestStrictFilterFlow::test_pass_includes_stop_loss` |
| no-look-ahead pivot | 기존 | `TestBasePivot` (기존) |
| legacy SELL valid | 기존 | `TestSellSignal*` (기존) |
| market BEAR blocks | 1 | `TestMarketGate::test_bear_blocks` |
| CAUTION blocks BREAKOUT | 1 | `TestMarketGate::test_caution_blocks_breakout` |

---

## Validation

각 Phase 종료 시:

```bash
cd stock-scanner
python -m compileall scanner database web
pytest -q
```

Phase 4 완료 시 추가:

```bash
# 마이그레이션 무결성: 기존 DB 파일에 _migrate() 적용 후 컬럼 확인
cd stock-scanner
python -c "
from sqlalchemy import create_engine, inspect
from database.models import init_db
init_db()
eng = create_engine('sqlite:///stock_scanner.db')
cols = {c['name'] for c in inspect(eng).get_columns('scan_results')}
need = {'stop_loss','sector_name','sector_stage','rs_trend','rs_zero_crossed','strict_filter_passed','filter_reasons'}
missing = need - cols
assert not missing, f'missing columns: {missing}'
print('OK')
"

# 수동 smoke: 전체 스캔 1회 실행, 시그널 수와 거부 사유 분포 출력
python -m scanner.scan_engine --once 2>&1 | tail -50
sqlite3 stock_scanner.db "SELECT strict_filter_passed, COUNT(*) FROM scan_results
                          WHERE scan_time > datetime('now','-1 hour')
                          GROUP BY strict_filter_passed;"
```

## Phasing

| Phase | 테마 | 파일 수 | 의존 |
|---|---|---|---|
| **1** | Config flags + DB 컬럼 + 신호 dict 스캐폴드 + 계획 문서 복사 | 4 (config, models, weinstein, plan doc) | — |
| **2** | Stop-loss 계산 + RS zero-cross + RS gates 단위 | 3 (weinstein, strict_filter 신규, tests) | Phase 1 |
| **3** | Stage·Extension·Volume·Base·Sector·Market gate 전부 strict_filter로 통합 | 3 (strict_filter, weinstein 보조, tests) | Phase 2 |
| **4** | scan_engine 흐름 변경 + STRICT_PERSIST_REJECTED + 알림 가드 + 통합 테스트 + 전략 문서 정정 | 4 (scan_engine, telegram, weinstein.md, tests) | Phase 3 |

각 Phase는 독립 PR. Phase 1만 머지되어도 동작 변화 없음(no-op). Phase 4가 머지되어야 strict 모드가 켜짐.

---

## Risks

1. **시그널 수 급감**: STRICT_WEINSTEIN_MODE=True 첫 운영 시 일평균 시그널이 0~1개로 떨어질 수 있다. 사용자 결정대로 의도된 결과지만 운영자가 즉시 알아채야 함. 완화: Phase 4 PR description에 "expect dramatic signal-count reduction" 명시 + STRICT_PERSIST_REJECTED=True 24시간 권장.
2. **RS zero-cross 정의 모호성**: 8주 안에 한 번만 전환하면 통과 vs 마지막 4주 안의 전환만 인정할지. CLAUDE.md(:184)는 "recent RS zero-line cross"만 명시. → Phase 2 구현 시 기본 8주, 환경변수 `RS_ZERO_CROSS_LOOKBACK_WEEKS`로 조정 가능. 단위 테스트로 정확한 의미 고정.
3. **Stop-loss vs 매수가 sanity**: 합성 픽스처에서 base_low가 현재가에 너무 가까우면 stop_loss >= price → None. analyze_stock 출력 None인데 STRICT_REQUIRE_STOP_LOSS=True면 거부. 의도된 결과지만 BREAKOUT 픽스처 일부에 영향 가능. 완화: 기존 BREAKOUT 테스트 픽스처 점검·필요시 `_make_stage2_base` 보강.
4. **legacy fast-path와 strict_filter 중복 평가**: `_get_market_filter_decision`이 BEAR 시 analyze_stock 진입 전에 막음 → strict_filter는 결과를 못 봄. 의도된 비용 절약이지만 STRICT_PERSIST_REJECTED=True일 때 BEAR 거부가 DB에 안 남음. 완화: Phase 4에서 STRICT_PERSIST_REJECTED=True일 때 BEAR fast-path 스킵하는 분기 추가(또는 이번 plan에서는 거부 사유 한정 — 문서화).
5. **DB 마이그레이션 실패**: SQLite ALTER TABLE 자체는 안전하지만 잘못된 DDL 문자열로 _migrate 실패 시 init_db 전체 실패 → 앱 부팅 불가. 완화: Phase 1 PR 단위 테스트가 인메모리 SQLite에서 ALTER를 검증.
6. **JSON `filter_reasons` 스키마 드리프트**: enum 문자열이 미래에 바뀌면 BI/대시보드가 깨짐. 완화: Phase 1에서 reason 상수를 `strict_filter.py`의 모듈-레벨 상수로 정의하고, 변경 시 changelog 의무화.

---

## Rollback Notes

Phase별 독립 롤백 가능.

- **Phase 4 롤백**: `git revert` 한 번. scan_engine 흐름이 원복되고 strict 게이트가 무력화. 신규 DB 컬럼은 남지만 NULL — 동작 영향 없음.
- **Phase 3 롤백**: `strict_filter.py`의 게이트 함수가 모두 통과(passed=True 반환)하도록 강제하는 핫픽스 또는 git revert. Phase 4가 살아있다면 STRICT_WEINSTEIN_MODE=False 환경변수만으로도 동등 효과.
- **Phase 2 롤백**: 신규 헬퍼만 죽음. signal dict의 stop_loss/rs_zero_crossed가 None으로 돌아감. strict_filter의 stop-loss/zero-cross 게이트가 그 결과 항상 fail → 거의 모든 signal이 거부됨. → Phase 4도 함께 롤백 필요.
- **Phase 1 롤백**: DB 컬럼 7개 NULL인 채로 남음(SQLite는 DROP COLUMN 미지원). 기능적으론 무영향. 수동 정리하려면 `CREATE TABLE ... AS SELECT` 필요.

긴급 hotfix 경로(코드 변경 없이): `STRICT_WEINSTEIN_MODE=False` 환경변수 1개로 strict 모드 비활성화 → legacy 동작 즉시 복귀. Phase 4 머지 후 첫 24시간은 이 경로를 운영팀이 알고 있어야 함.

---

## Critical Files (참조)

| Path | 역할 |
|---|---|
| `stock-scanner/config.py` | 14개 STRICT_* 플래그 정의 |
| `stock-scanner/scanner/weinstein.py` | 신호 dict 스캐폴드, stop-loss/zero-cross 헬퍼 |
| `stock-scanner/scanner/strict_filter.py` (신규) | 8개 게이트 결정 함수 |
| `stock-scanner/scanner/scan_engine.py` | apply_strict_filter 호출, persist/notify 분기 |
| `stock-scanner/database/models.py` | 7개 컬럼 + `_migrate()` 확장 |
| `stock-scanner/notifications/telegram.py` | 알림 가드(이미 strict-pass만 받음) |
| `stock-scanner/scanner/market_analysis.py` | `_get_market_filter_decision` 정합성 |
| `stock-scanner/tests/test_weinstein.py` | stop-loss / zero-cross / scaffold 테스트 |
| `stock-scanner/tests/test_strict_filter.py` (신규) | 게이트별 단위 + 통합 |
| `stock-scanner/tests/test_scan_engine.py` | persist/notify 분기 |
| `docs/plans/strict-weinstein-optimal-buy-filter.md` | 이 계획 |
| `docs/strategy/weinstein.md` | 거래량 hard block 표현 정정 (Phase 4) |

---

## End-to-End 검증 (모든 Phase 후)

```bash
cd stock-scanner
python -m compileall scanner database web
pytest -v
# 신규: 17 strict 카테고리 모두 PASSED 확인
pytest tests/test_strict_filter.py -v

# 운영 검증
STRICT_WEINSTEIN_MODE=true STRICT_PERSIST_REJECTED=true python -m scanner.scan_engine --once
sqlite3 stock_scanner.db <<'SQL'
SELECT
  strict_filter_passed,
  signal_type,
  COUNT(*) AS n
FROM scan_results
WHERE scan_time > datetime('now','-1 hour')
GROUP BY strict_filter_passed, signal_type;

SELECT filter_reasons, COUNT(*) AS n
FROM scan_results
WHERE strict_filter_passed = 0
  AND scan_time > datetime('now','-1 hour')
GROUP BY filter_reasons
ORDER BY n DESC
LIMIT 10;
SQL

# 차트 API smoke
uvicorn web.app:app --reload &
curl -s 'http://localhost:8000/api/chart/ohlcv?market=US&ticker=AAPL&timeframe=daily&range=1y' | jq '.candles | length'
```
