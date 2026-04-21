# Weinstein Scanner — 기술 문서

> 버전: 2.0 | 마지막 업데이트: 2026-04

---

## 1. 고수준 흐름 (High-Level Flow)

```
[스케줄러 / 수동 스캔]
        │
        ▼
scan_engine.run_scan()
        │
        ├─► get_market_stages()          ← 시장 지수 Stage 판단 (Forest to Trees)
        │       SPY / QQQ / KOSPI200
        │       → US_condition, KR_condition
        │
        ├─► get_benchmark_close()        ← RS 계산용 벤치마크 로드
        │
        ├─► _scan_kr() / _scan_us()
        │       │
        │       ├─► get_all_kr_tickers() / get_all_us_tickers()
        │       │
        │       ├─► get_kr_ohlcv() / get_us_batch()
        │       │
        │       ├─► analyze_stock()      ← Weinstein 시그널 분석
        │       │       _build_indicators()
        │       │       _find_breakout_signal()
        │       │       _find_rebreakout_signal()
        │       │       _find_rebound_signal()
        │       │
        │       └─► _get_market_filter_decision()  ← BULL/BEAR/CAUTION 필터
        │               allow? → _save() → DB
        │
        ├─► _check_watchlist()           ← 감시 종목 매도 시그널
        │
        └─► _notify()                   ← Telegram 알림
```

---

## 2. 시그널 로직

### 2.1 BREAKOUT (돌파) 🚀

**개념**

주가가 일정 기간 base(횡보/압축 구간)를 형성한 후, base의 최고점(pivot)을
거래량을 동반해 상향 돌파하는 시점. Stage 1→2 전환 또는 강한 Stage2에서 발생.

**정확한 조건**

| 조건 | 설명 |
|------|------|
| `price > MA150` | 주가가 30주(150일) MA 위 |
| `MA150 slope > 0` | MA150 상승 방향 |
| `price > MA50` | 50일 MA 위 (REQUIRE_PRICE_ABOVE_MA50=true) |
| `close[-1] <= pivot_high` | 전날은 base 최고점 이하 |
| `close[signal_day] > pivot_high` | 시그널일에 pivot 돌파 |
| `volume_ratio >= BREAKOUT_VOLUME_RATIO` | 거래량 확장 (기본 1.5x) |
| `extension < BREAKOUT_MAX_EXTENDED_PCT` | MA150 대비 과매수 아님 (기본 15%) |

**이유**: 단순 MA 교차만으로는 가짜 신호가 많음. Base + Pivot + Volume 삼중 조건으로
기관 매수가 수반된 실질적 돌파만 포착.

**예시**

```
가격:   94─95─96─95─96─97─[104]  ← 돌파일 (pivot=97 위)
MA150:  88─88─89─89─89─89─[90]   ← 우상향
거래량: 50─48─52─45─48─50─[150]  ← 3x 급증
                                  ✅ BREAKOUT
```

---

### 2.2 RE_BREAKOUT (재돌파) 🔁

**개념**

Stage2 진행 중 단기 조정/횡보(continuation base)를 거친 후,
continuation base의 고점을 재돌파하는 시점. 추세 지속 매매 진입.

**정확한 조건**

| 조건 | 설명 |
|------|------|
| `stage == STAGE2` | 반드시 Stage2 |
| `price > MA150 && price > MA50` | 두 MA 모두 위 |
| `MA150 slope > 0` | MA150 상승 중 |
| `pullback_pct` 3%~`REBREAKOUT_MAX_PULLBACK_PCT` | 조정폭 적절 |
| `close[-1] <= continuation_pivot` | 전날은 base 고점 이하 |
| `close[signal_day] > continuation_pivot` | 재돌파 |
| `volume_ratio >= REBREAKOUT_VOLUME_RATIO` | 거래량 확장 |
| (선택) volume dry-up in base | 조정 중 거래량 감소 확인 |

**이유**: 깊은 조정은 추세 훼손 가능성이 있어 제외. Stage2 진행 중
쉬어가는 구간(continuation base)을 포착해 재진입.

**예시**

```
Stage2:  100─105─110─[조정]─106─107─[109]  ← 재돌파
                     └──continuation base──┘ (pivot=108)
거래량:  보통──────────────낮음─────────[높음]
                                        ✅ RE_BREAKOUT
```

---

### 2.3 REBOUND (눌림목 반등) 🔄

**개념**

Stage2에서 주가가 MA50(10주선)까지 눌린 후 반등을 확인하는 시점.
단기 눌림 매수의 진입 타이밍.

**알고리즘 (시간순 = 과거→현재)**

```
Phase 1: 눌림 감지
  price가 MA50 × (1 ± REBOUND_TOUCH_PCT%) 이내로 접근
  단, price > MA150 × 0.95 (Stage2 컨텍스트 유지)

Phase 2: 저점 추적
  눌림 구간의 최저가(touched_low) 갱신

Phase 3: 반등 확인
  price >= touched_low × (1 + REBOUND_CONFIRM_PCT%) AND price > MA50
  → 시그널 발생
```

**정확한 조건**

| 조건 | 설명 |
|------|------|
| `stage in (STAGE2, STAGE3)` | Stage2/3 컨텍스트 |
| `price >= MA150 × 0.95` | MA150 대비 5% 이상 하락 시 리셋 |
| `MA50×(1-MAX_PCT) <= price <= MA50×(1+TOUCH_PCT)` | MA50 ±3% 이내 터치 |
| `pullback < REBOUND_MAX_PULLBACK_PCT` | 최대 12% 이내 조정 |
| `rebound >= REBOUND_CONFIRM_PCT%` | 저점 대비 2% 이상 반등 |
| `price > MA50` | 반등 확인 시 MA50 위 |

**⚠️ 구버전 버그 수정**: 이전 코드는 최신→과거 순서로 반복해 "반등 후 눌림"을 잘못
감지했음. 현재 코드는 과거→현재 시간순으로 스캔하여 "눌림 → 반등 확인" 정상 감지.

**예시**

```
Stage2:  115─114─110─[103]─105─[108]  ← 반등 확인일
                       ↑ MA50≈104 근처 터치
Phase:   ────────────T──────────────R  (T=Touch, R=Rebound)
                                    ✅ REBOUND (support=104)
```

---

## 3. 시장 필터 (Forest to Trees)

### 시장 Stage 계산

`market_analysis.get_market_stages()`가 미국(SPY, QQQ)과 한국(KODEX200) 지수에
동일한 `stage_of()` 로직 적용:

```
BEAR    🔴 — 모든 지수가 STAGE4
CAUTION 🟡 — 하나라도 STAGE4, 또는 혼조
BULL    🟢 — 모든 지수가 STAGE2
NEUTRAL 🔵 — 모든 지수가 STAGE1 또는 STAGE2
```

결과는 60분 캐시됨.

### 시그널에 대한 영향

| 시장 상태 | 설정 | 동작 |
|----------|------|------|
| BEAR | BLOCK_NEW_BUYS_IN_BEAR=true | 모든 BUY 시그널 차단 |
| CAUTION | CAUTION_MODE=block_breakout | BREAKOUT만 차단 |
| CAUTION | CAUTION_MODE=allow_with_flag | 허용 + ⚠️ 플래그 |
| CAUTION | CAUTION_MODE=allow_all | 모두 허용 |
| BULL / NEUTRAL | — | 항상 허용 |
| * | ENABLE_MARKET_FILTER=false | 필터 비활성화 |

---

## 4. 설정 변수 (config.py)

### 핵심

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `MA_PERIOD` | 150 | Weinstein 30주 MA |
| `SCAN_LOOKBACK_DAYS` | 7 | 최근 N일 이내 시그널 탐색 |
| `VOLUME_AVG_PERIOD` | 20 | 평균 거래량 기산 기간 |

### BREAKOUT

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `BREAKOUT_BASE_LOOKBACK_DAYS` | 60 | base 확인 기간 (일) |
| `BREAKOUT_MIN_BASE_DAYS` | 15 | 최소 base 길이 |
| `BREAKOUT_VOLUME_RATIO` | 1.5 | 최소 거래량 배율 |
| `BREAKOUT_MAX_EXTENDED_PCT` | 15.0 | MA150 대비 최대 과매수 (%) |
| `REQUIRE_PRICE_ABOVE_MA50` | true | MA50 위 조건 필수 여부 |

### RE_BREAKOUT

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `REBREAKOUT_BASE_LOOKBACK_DAYS` | 30 | continuation base 기간 |
| `REBREAKOUT_MAX_PULLBACK_PCT` | 15.0 | 최대 허용 조정폭 (%) |
| `REBREAKOUT_VOLUME_RATIO` | 1.5 | 최소 거래량 배율 |
| `REBREAKOUT_REQUIRE_VOLUME_DRYUP` | false | 조정 중 거래량 감소 필수 |

### REBOUND

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `REBOUND_MA_PERIOD` | 50 | 지지선 MA 기간 |
| `REBOUND_TOUCH_PCT` | 3.0 | MA50 ±N% 이내를 터치로 인정 |
| `REBOUND_CONFIRM_PCT` | 2.0 | 저점 대비 반등 확인 임계값 (%) |
| `REBOUND_MAX_PULLBACK_PCT` | 12.0 | 최대 허용 조정폭 (%) |
| `REBOUND_REQUIRE_VOLUME_DRYUP` | false | 눌림 중 거래량 감소 필수 |

### Market Filter

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `ENABLE_MARKET_FILTER` | true | 시장 필터 활성화 |
| `BLOCK_NEW_BUYS_IN_BEAR` | true | BEAR 시 신규 BUY 차단 |
| `CAUTION_MODE` | allow_with_flag | CAUTION 처리 방식 |

---

## 5. 코드 구조

### scanner/weinstein.py

| 함수 | 역할 |
|------|------|
| `_slope()` | MA 기울기(% / bar) 계산 |
| `stage_of()` | Weinstein Stage 분류 (STAGE1~4) |
| `calc_rs()` | 상대강도 계산 (주식 수익률 / 지수 수익률) |
| `_build_indicators()` | MA150, MA50, vol_avg 등 지표 dict 반환 |
| `_find_breakout_signal()` | BREAKOUT 탐지 (pivot/base 기반) |
| `_find_rebreakout_signal()` | RE_BREAKOUT 탐지 (continuation base) |
| `_find_rebound_signal()` | REBOUND 탐지 (MA50 지지 + 시간순 스캔) |
| `_signal_quality()` | STRONG / MODERATE / WEAK 품질 점수 |
| `analyze_stock()` | 공개 API: 단일 종목 BUY 시그널 탐지 |
| `check_sell_signal()` | 공개 API: 감시 종목 SELL 시그널 탐지 |

### scanner/scan_engine.py

| 함수 | 역할 |
|------|------|
| `run_scan()` | 메인 진입점: 시장/유니버스 선택해 전체 스캔 |
| `_scan_kr() / _scan_us()` | 국내/미국 종목 반복 스캔 |
| `_check_watchlist()` | 감시 종목 매도 체크 |
| `_get_market_filter_decision()` | BULL/BEAR/CAUTION 기반 허용 여부 |
| `_save()` | DB 저장 (중복 방지 + 메타데이터 포함) |
| `_notify()` | Telegram 알림 포맷팅 |

### scanner/market_analysis.py

| 함수 | 역할 |
|------|------|
| `get_market_stages()` | SPY/QQQ/KOSPI200 Stage 분석 (60분 캐시) |
| `get_benchmark_close()` | RS 계산용 벤치마크 종가 시리즈 |
| `_analyze_index()` | 개별 지수 Stage/기울기/52주 위치 계산 |
| `_condition()` | 지수 목록으로 BULL/BEAR/CAUTION/NEUTRAL 판단 |

### config.py

모든 설정을 환경변수로 관리. `.env` 파일 또는 OS 환경변수로 오버라이드 가능.
기본값은 실전 사용에 바로 쓸 수 있는 수준.

---

## 6. 확장 가이드

### 새 시그널 추가

1. `weinstein.py`에 헬퍼 함수 작성:
   ```python
   def _find_xxx_signal(ind: dict) -> Optional[dict]:
       # ind에서 close, ma50, ma150 등 사용
       return {
           "signal_type": "XXX",
           "signal_date": "YYYY-MM-DD",
           "vol_ratio":   float,
           "pivot_price": float | None,
       }
   ```
2. `analyze_stock()` 탐지 체인에 추가:
   ```python
   sig = (
       _find_breakout_signal(ind)
       or _find_rebreakout_signal(ind)
       or _find_rebound_signal(ind)
       or _find_xxx_signal(ind)   # ← 추가
   )
   ```
3. `config.py`에 관련 설정 추가 후 `.env.example` 반영
4. `tests/test_weinstein.py`에 테스트 클래스 추가

### 임계값 조정

`.env` 파일에서 변경 후 서버 재시작:

```bash
# .env
BREAKOUT_VOLUME_RATIO=2.0       # 더 엄격한 거래량 기준
REBOUND_MAX_PULLBACK_PCT=8.0    # 더 얕은 조정만 허용
CAUTION_MODE=block_breakout     # CAUTION 시 돌파 차단
```

### 새 시장 필터 추가

`scan_engine._get_market_filter_decision()` 내에 조건 삽입:

```python
# 예: 섹터 기반 필터
if signal.get("sector") == "TECH" and tech_risk_high:
    return False, "Tech 섹터 위험"
```

---

## 7. 트레이드오프 / 향후 개선

| 항목 | 현재 상태 | 개선 방향 |
|------|----------|----------|
| Pivot 계산 | Rolling max | Swing High / Volume Profile 기반 정교화 |
| RS 필터링 | 계산만, 필터 미적용 | `REQUIRE_RS_POSITIVE` 설정으로 RS>1 종목만 허용 |
| REBOUND 반등 임계값 | 고정 % | ATR(Average True Range) 기반 동적 임계값 |
| 스캔 속도 | KR 순차 0.05s, US batch 50 | 비동기 병렬 처리 |
| 백테스트 | 없음 | OHLCV 히스토리로 시그널 정확도 검증 |
