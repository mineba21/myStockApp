# Weinstein Stage Scanner — 프로젝트 개요 (AI 협업용)

> **대상 독자**: Codex, Claude, GPT 등 이 코드베이스를 처음 보는 AI  
> **목적**: 앱 전체 흐름·파일 역할·DB 스키마·API 엔드포인트·업데이트 이력을 한 문서에서 파악  
> **마지막 업데이트**: 2026-04-18

---

## 1. 프로젝트 한 줄 요약

**Stan Weinstein의 Stage Analysis** 기법을 자동화한 주식 스캐너.  
한국(KOSPI/KOSDAQ) + 미국(S&P500/NASDAQ100 등) 전종목을 매일 3회 스캔해,  
Stage2 진입 매수 시그널(BREAKOUT / RE_BREAKOUT / REBOUND)을 탐지하고  
텔레그램으로 알림 + 웹 UI에서 결과 확인.

---

## 2. 디렉터리 구조

```
stock-scanner/
├── main.py                   # 진입점 — uvicorn 서버 시작
├── config.py                 # 모든 설정값 (env → 변수)
├── scheduler.py              # APScheduler KST 09:00/14:00/22:00 자동 스캔
├── requirements.txt
├── .env.example              # 환경변수 템플릿
│
├── scanner/
│   ├── weinstein.py          # ★ 핵심 — Stage 분석 엔진
│   ├── scan_engine.py        # 스캔 오케스트레이션 + DB 저장 + 알림
│   ├── market_analysis.py    # 시장/섹터 지수 Stage 분석 (Forest-to-Trees)
│   ├── kr_stocks.py          # 한국 종목 목록 + OHLCV 수집
│   └── us_stocks.py          # 미국 종목 목록 + OHLCV 수집 (yfinance)
│
├── database/
│   └── models.py             # SQLAlchemy 모델 + init_db + _migrate
│
├── notifications/
│   └── telegram.py           # 텔레그램 봇 메시지 발송
│
├── web/
│   ├── app.py                # FastAPI 라우터 (API + HTML 서빙)
│   ├── templates/index.html  # AlpineJS + TailwindCSS 단일 페이지 앱
│   └── static/               # CSS/JS 정적 파일
│
├── trading/                  # KIS API 연동 (현재 미완성, stub)
│
├── tests/
│   └── test_weinstein.py     # pytest 34개 테스트 (합성 데이터)
│
└── docs/
    └── weinstein_scanner.md  # 시그널 로직 상세 문서
```

---

## 3. 전체 데이터 흐름

```
[트리거]
  ├─ APScheduler (09:00 / 14:00 / 22:00 KST)
  └─ 웹 UI "스캔 시작" 버튼  →  POST /api/scan/start

        ↓
[scan_engine.run_scan()]
  1. market_analysis.get_market_stages()  ←  SPY, QQQ, KODEX200 + 섹터 ETF Stage 분석
  2. get_benchmark_close("KR"/"US")       ←  RS 계산용 기준 종가

  3. _scan_kr() 또는 _scan_us()
     ├─ kr_stocks.get_all_kr_tickers()   ←  KRX finder_stkisu API (필터 적용)
     │   or us_stocks.get_all_us_tickers()  ←  Wikipedia + FinanceDataReader
     ├─ OHLCV 수집 (pykrx / yfinance 배치)
     └─ weinstein.analyze_stock(df, ...)
          ├─ _build_indicators()  → MA150, MA50, volume_avg, slope
          ├─ _find_breakout_signal()
          ├─ _find_rebreakout_signal()
          └─ _find_rebound_signal()

  4. _get_market_filter_decision()  →  BEAR 차단 / CAUTION 플래그
  5. _grade(signal)                 →  S / A / B 등급
  6. _save(db, signal)              →  ScanResult DB 저장
  7. _check_watchlist()             →  감시목록 매도 시그널 체크

  8. _notify(buys, sells, send_fn)  →  텔레그램 메시지 발송
        ↓
[웹 UI]  GET /api/results  →  결과 테이블 조회
```

---

## 4. 핵심 모듈 상세

### 4-1. `scanner/weinstein.py` — Stage 분석 엔진

#### 공개 함수

| 함수 | 설명 |
|------|------|
| `analyze_stock(df, ticker, name, market, benchmark_close, market_condition)` | 매수 시그널 탐지. 시그널 없으면 `None` 반환 |
| `check_sell_signal(df, ticker, name, market, buy_price, stop_loss)` | 감시종목 매도 시그널 체크 |
| `stage_of(price, ma, slope)` | STAGE1~4 분류 |
| `calc_rs(close, benchmark_close, period=65)` | 상대강도(RS) 계산 |

#### `analyze_stock` 반환 dict 주요 필드

```python
{
    "ticker", "name", "market",
    "signal_type",    # "BREAKOUT" | "RE_BREAKOUT" | "REBOUND"
    "stage",          # "STAGE2" (거의 항상)
    "price",          # 현재가
    "ma150",          # 150일 이동평균
    "ma50",           # 50일 이동평균  ← v3 추가
    "ma_slope",       # MA150 기울기 (%/bar)
    "volume_ratio",   # 거래량 배율
    "signal_date",    # 시그널 발생일 "YYYY-MM-DD"
    "rs",             # 상대강도 (None 가능)
    "rs_value",       # legacy ratio RS 저장/API alias
    "mansfield_rs",   # Weinstein v2 Mansfield RS
    "weekly_stage",   # 주봉 30-SMA 기준 Stage
    "weekly_volume_ratio",
    "pivot_price",    # BREAKOUT/RE_BREAKOUT 기준가
    "support_level",  # MA50 지지선
    "base_weeks",
    "base_width_pct",
    "base_quality",   # "STRONG" | "WEAK" | "N/A"  ← v3 추가
    "signal_quality", # "STRONG" | "MODERATE" | "WEAK"
    "market_condition",  # "BULL" | "BEAR" | "CAUTION" | "NEUTRAL"
    "rs_passed",      # RS >= 1.0 여부
    "warning_flags",  # v2 경고 목록
}
```

#### 시그널별 조건 요약

**BREAKOUT** (우선순위 1)
- `price > MA150`, `MA150 slope > 0`
- `price > MA50` (REQUIRE_PRICE_ABOVE_MA50=true 시)
- 최근 SCAN_LOOKBACK_DAYS 이내에 base 고점(pivot) 상향 돌파
- 돌파일 거래량 ≥ BREAKOUT_VOLUME_RATIO × 20일 평균 (기본 1.5배)
- 돌파일 종가 ≥ 당일 고가 × 0.70 (긴 윗꼬리 제외)
- MA150 대비 과매수 < BREAKOUT_MAX_EXTENDED_PCT (기본 15%)
- 직전 10일 중 7일 이상 MA150 ±5% 횡보 → `base_quality=STRONG`

**RE_BREAKOUT** (우선순위 2)
- 현재 STAGE2 상태에서 단기 조정(3~15%) 후 continuation 고점 돌파
- `price > MA50`
- 조정폭 3%~REBREAKOUT_MAX_PULLBACK_PCT (기본 15%)

**REBOUND** (우선순위 3)
- STAGE2 + MA150 slope > 0.02
- **저가(low)**가 `MA50 × (1 - TOUCH_PCT%)` 이하로 터치 (TOUCH_PCT 기본 3%)
- 터치 후 close가 MA50 위로 회복 + 저점 대비 CONFIRM_PCT% 이상 반등
- 반등 확인일 거래량 ≥ 평균 × 1.3

#### `check_sell_signal` — severity 레벨

| severity | 조건 |
|----------|------|
| `HIGH`   | 손절가 도달 또는 MA150 하향 이탈(Stage4 진입) |
| `MEDIUM` | MA150 기울기 반전 (5일 전 양수 → 현재 ≤ 0) |
| `LOW`    | Stage3 징후 (고점 부근, 분배 단계) |

---

### 4-2. `scanner/scan_engine.py` — 오케스트레이션

#### 주요 함수

| 함수 | 설명 |
|------|------|
| `run_scan(market, universe, triggered_by)` | 메인 스캔 진입점. 중복 실행 방지 |
| `_scan_kr(db, benchmark_close, market_condition)` | KR 종목 순환 스캔 |
| `_scan_us(db, universe, benchmark_close, market_condition)` | US 종목 배치 스캔 |
| `_check_watchlist(db)` | 감시목록 매도 체크 |
| `_get_market_filter_decision(condition, signal_type)` | BEAR/CAUTION 필터 |
| `_grade(signal)` | S/A/B 종합 등급 계산 |
| `_save(db, signal)` | DB 저장 (중복 시 가격/등급만 업데이트) |
| `_notify(buys, sells, send_fn)` | 텔레그램 메시지 포맷 + 발송 |
| `_sector_summary(market)` | 강세/약세 섹터 한 줄 요약 |

#### `_grade()` 점수 기준

```
signal_quality:  STRONG=3 / MODERATE=2 / WEAK=1
signal_type:     BREAKOUT → +1
base_quality:    STRONG   → +1
rs:              ≥1.5 → +1 / ≥1.0 → +0.5
market_condition: BULL → +1 / BEAR → -2

S: 총점 ≥ 6
A: 총점 ≥ 4
B: 그 외
```

#### 시장 필터 (`CAUTION_MODE`)

| 모드 | 동작 |
|------|------|
| `block_breakout` | CAUTION 장세에서 BREAKOUT만 차단, REBOUND/RE_BREAKOUT 허용 |
| `allow_with_flag` | 모두 허용하되 `⚠️ CAUTION 장세` 경고 추가 (기본값) |
| `allow_all` | 필터 없음 |
| — (BEAR) | `BLOCK_NEW_BUYS_IN_BEAR=true`이면 모든 매수 시그널 차단 |

---

### 4-3. `scanner/market_analysis.py` — Forest-to-Trees

시장 전체 방향성을 개별 종목 스캔 전에 확인.

**분석 대상**

| 구분 | 종목 |
|------|------|
| US 지수 | SPY (S&P500), QQQ (NASDAQ100) |
| KR 지수 | KODEX200 (069500) |
| US 섹터 ETF | XLK(기술), XLF(금융), XLV(헬스케어), XLE(에너지), XLI(산업재), XLY(경기소비재) |
| KR 섹터 ETF | 091160(반도체), 305720(2차전지), 244580(바이오) |

**`get_market_stages()` 반환 구조**

```python
{
    "US":           [...],          # 지수별 Stage 정보
    "KR":           [...],
    "US_SECTORS":   [...],          # 섹터 ETF Stage 정보
    "KR_SECTORS":   [...],
    "US_condition": "BULL",         # BULL | BEAR | CAUTION | NEUTRAL
    "KR_condition": "NEUTRAL",
    "updated_at":   "2026-04-18T...",
}
```

---

### 4-4. `scanner/kr_stocks.py` — 한국 종목

**필터 파이프라인** (순서대로 적용)

1. **KRX API** 전종목 조회 (`finder_stkisu` 엔드포인트 — 세션 불필요)
2. **키워드 필터**: 스팩·SPAC·리츠·ETF·ETN·선물·인버스·레버리지 제외
3. **시가총액 필터**: ≥ 1,000억 원 (pykrx `get_market_cap_by_ticker`)
4. **가격 필터**: 종가 ≥ 1,000원

> pykrx API 실패 시 3·4단계 생략하고 안전하게 진행

**OHLCV**: FinanceDataReader 우선 → pykrx fallback, 2년치

---

### 4-5. `scanner/us_stocks.py` — 미국 종목

**유니버스 옵션** (`.env`의 `US_UNIVERSE`)

| 값 | 종목 수 (대략) |
|----|---------------|
| `sp500+nasdaq100` | ~600개 (기본값) |
| `sp500+nasdaq100+nyse+nasdaq` | ~6,000개+ |
| `all` | 위와 동일 |

- `EXCLUDE_US = {'GOOGL'}`: GOOG와 중복 심볼 제거
- yfinance 50개 배치 다운로드 (`yf.download`)
- 결과 dict 캐시 (universe_key → list)

---

## 5. 데이터베이스 스키마

**DB 경로**: `/Users/mac/.stock_scanner.db` (SQLite)  
SQLAlchemy `_migrate()`로 구버전 DB에 새 컬럼 자동 추가.

### `scan_results` — 매수 시그널 저장

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | INTEGER PK | |
| scan_time | DATETIME | 스캔 시각 (UTC) |
| market | VARCHAR(10) | KR / US |
| ticker | VARCHAR(20) | 종목 코드 |
| name | VARCHAR(100) | 종목명 |
| signal_type | VARCHAR(20) | BREAKOUT / RE_BREAKOUT / REBOUND |
| stage | VARCHAR(10) | STAGE2 등 |
| price | FLOAT | 현재가 |
| ma150 | FLOAT | 150일 이동평균 |
| volume | FLOAT | 당일 거래량 |
| volume_avg | FLOAT | 20일 평균 거래량 |
| volume_ratio | FLOAT | 거래량 배율 |
| signal_date | VARCHAR(10) | 시그널 발생일 YYYY-MM-DD |
| notified | BOOLEAN | 텔레그램 알림 여부 |
| pivot_price | FLOAT | 돌파 기준 pivot 가격 |
| support_level | FLOAT | MA50 지지선 |
| market_condition | VARCHAR(20) | BULL/BEAR/CAUTION/NEUTRAL |
| signal_quality | VARCHAR(10) | STRONG/MODERATE/WEAK |
| rs_value | FLOAT | legacy ratio RS 값 |
| grade | VARCHAR(5) | S / A / B 종합 등급 |
| weekly_stage | VARCHAR(10) | 주봉 30-SMA 기준 Stage |
| sma30w / sma10w | FLOAT | 30주 / 10주 SMA |
| weekly_volume_ratio | FLOAT | 주봉 거래량 배율 |
| mansfield_rs | FLOAT | Weinstein v2 Mansfield RS |
| rs_trend | VARCHAR(10) | RISING / FALLING / FLAT |
| base_weeks / base_width_pct | FLOAT | 주봉 base 기간 / 폭 |
| warning_flags | TEXT | JSON list 형태 경고 목록 |

### `scan_logs` — 스캔 실행 기록

| 컬럼 | 설명 |
|------|------|
| id, started_at, finished_at | |
| market | 스캔 대상 (KR/US/ALL) |
| total_scanned, signals_found | |
| status | RUNNING / DONE / ERROR |
| triggered_by | manual / scheduler |
| error_msg | 오류 메시지 |

### `accounts` — 계좌

| 컬럼 | 설명 |
|------|------|
| id, name, broker, memo | |
| account_type | KR_STOCK / US_STOCK / KR_PENSION / KR_IRP / KR_ISA / OTHER |
| currency | KRW / USD (account_type으로 자동 결정) |
| is_active | soft delete |

### `transactions` — 거래 내역

| 컬럼 | 설명 |
|------|------|
| id, account_id (FK) | |
| tx_type | BUY / SELL / DEPOSIT / WITHDRAW |
| trade_date | YYYY-MM-DD |
| ticker, name, market | 종목 정보 (입출금 시 NULL) |
| quantity, price, amount | 수량·단가·총금액 |
| fee, tax | 수수료·세금 |

### `holdings` — 보유 주식

| 컬럼 | 설명 |
|------|------|
| id, account_id (FK) | |
| ticker, name, market | |
| quantity | 보유 수량 |
| avg_price | 평단가 (매수 시 자동 재계산) |
| current_price | 현재가 캐시 |
| is_active | soft delete |

### `watchlist` — 매도 감시 목록

| 컬럼 | 설명 |
|------|------|
| ticker (UNIQUE) | |
| buy_price, stop_loss, target_price | 매수가·손절가·목표가 |
| is_active | |

---

## 6. REST API 엔드포인트

Base URL: `http://localhost:8000`

### 스캔

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/api/scan/start?market=KR&universe=kospi+kosdaq` | KR 스캔 시작 (백그라운드) |
| POST | `/api/scan/start?market=US&universe=sp500+nasdaq100` | US 스캔 시작 (백그라운드) |
| POST | `/api/scan/start?market=ALL` | 전체 스캔 (스케줄러 전용) |
| GET | `/api/scan/status` | 진행 상황 + 다음 스케줄 |
| GET | `/api/results?market=ALL&signal_type=ALL&days=7&limit=200` | 시그널 결과 목록 |
| DELETE | `/api/results/{id}` | 결과 삭제 |
| GET | `/api/scan/logs` | 스캔 실행 로그 |

### 계좌/거래/보유

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET/POST | `/api/accounts` | 계좌 목록·생성 |
| DELETE | `/api/accounts/{id}` | 계좌 비활성화 |
| GET/POST | `/api/transactions` | 거래 내역 목록·생성 |
| DELETE | `/api/transactions/{id}` | 거래 삭제 + 보유 재계산 |
| GET | `/api/holdings` | 보유 주식 목록 |
| PUT | `/api/holdings/{id}/price` | 현재가 업데이트 |

### 감시/알림

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET/POST | `/api/watchlist` | 감시목록 |
| DELETE | `/api/watchlist/{id}` | 감시 해제 |
| GET | `/api/market/stages` | 시장 지수 Stage 현황 |
| POST | `/api/telegram/test` | 텔레그램 연결 테스트 |

---

## 7. 설정값 (config.py / .env)

```ini
# 텔레그램
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# 공통 스캔
SCAN_LOOKBACK_DAYS=7          # 시그널 탐색 기간 (거래일)
MA_PERIOD=150                 # Weinstein 30주 MA
MA_SLOPE_PERIOD=10            # 기울기 계산 기간
VOLUME_AVG_PERIOD=20          # 거래량 평균 기간

# BREAKOUT
BREAKOUT_BASE_LOOKBACK_DAYS=60   # base 기간 최대 일수
BREAKOUT_MIN_BASE_DAYS=15        # base 최소 일수
BREAKOUT_VOLUME_RATIO=1.5        # 돌파 거래량 배율
BREAKOUT_MAX_EXTENDED_PCT=15.0   # MA150 대비 과매수 상한 (%)
REQUIRE_PRICE_ABOVE_MA50=true

# RE_BREAKOUT
REBREAKOUT_BASE_LOOKBACK_DAYS=30
REBREAKOUT_MAX_PULLBACK_PCT=15.0
REBREAKOUT_VOLUME_RATIO=1.5
REBREAKOUT_REQUIRE_VOLUME_DRYUP=false

# REBOUND
REBOUND_MA_PERIOD=50
REBOUND_TOUCH_PCT=3.0         # MA50 터치 인정 범위 (%)
REBOUND_CONFIRM_PCT=2.0       # 저점 대비 반등 확인 (%)
REBOUND_MAX_PULLBACK_PCT=12.0 # 최대 허용 조정폭
REBOUND_REQUIRE_VOLUME_DRYUP=false

# 시장 필터
ENABLE_MARKET_FILTER=true
BLOCK_NEW_BUYS_IN_BEAR=true
CAUTION_MODE=allow_with_flag   # block_breakout | allow_with_flag | allow_all

# 유니버스
US_UNIVERSE=sp500+nasdaq100    # sp500+nasdaq100+nyse+nasdaq | all

# 스케줄
SCHEDULE_TIMES=09:00,14:00,22:00   # KST

# KIS API (현재 미사용)
KIS_APP_KEY=
KIS_APP_SECRET=
KIS_ACCOUNT_NO=
KIS_IS_PAPER=true
```

---

## 8. 실행 방법

```bash
# 1. 의존성 설치
cd myStockApp/stock-scanner
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. 환경변수 설정
cp .env.example .env
# .env 파일에 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 입력

# 3. 서버 시작
python main.py
# → http://localhost:8000

# 4. 테스트
venv/bin/python -m pytest tests/ -v

# macOS: 더블클릭 실행
# 스캐너 시작.command
```

---

## 9. 업데이트 이력

### v1.0 — 초기 구축
- FastAPI + SQLite + AlpineJS/TailwindCSS SPA
- Weinstein Stage2 BREAKOUT 기본 로직
- KR(pykrx) + US(yfinance) 데이터 수집
- APScheduler 09:00/14:00/22:00 KST 자동 스캔
- 텔레그램 알림

### v1.1 — 경로 이동 + 초기 버그 수정
- `myStockApp/stock-scanner/` 폴더로 이동 후 경로 수정
- `.gitignore` — `*.db` 제외 항목 삭제 (DB를 git에 포함)
- ngrok 외부 접속 지원

### v1.2 — KR 종목 수집 버그 수정
- **문제**: `pykrx.get_market_ticker_list(date)` 가 세션 없이 "LOGOUT" 반환 → 종목 0개
- **해결**: KRX `finder_stkisu` 엔드포인트 직접 POST (날짜·세션 불필요, ~2,771개)

### v1.3 — US 유니버스 확장
- **문제**: `get_all_us_tickers('SP500')` 케이스 불일치 → 0개 반환
- **해결**: `key = universe.lower()` 추가
- **문제**: 캐시가 `list`였다가 universe 구분이 안 됨
- **해결**: 캐시를 `dict` (universe_key → list) 로 변경
- NYSE / NASDAQ 전체 종목 추가 (FinanceDataReader)

### v2.0 — Weinstein 전략 대규모 리팩터
- **BREAKOUT**: 단순 고점 돌파 → pivot/base 기반 돌파
  - base 기간(60일) 최고점(pivot) 계산
  - MA150 ±15% 과매수 필터
  - `REQUIRE_PRICE_ABOVE_MA50` 옵션
- **RE_BREAKOUT**: 신규 추가 — Stage2 continuation base 돌파
- **REBOUND**: 신규 추가 — MA50 눌림목 반등
  - 버그 수정: 과거→현재 시간순 스캔으로 수정 (기존 역방향 스캔 오류)
- **Market Filter**: BULL/BEAR/CAUTION/NEUTRAL
  - SPY, QQQ, KODEX200 Stage 분석
  - BEAR 시 매수 차단, CAUTION 시 플래그 또는 차단
- **Signal Quality**: STRONG/MODERATE/WEAK (거래량·기울기·RS 점수제)
- **RS**: 65거래일 상대강도 계산 추가
- DB 확장: pivot_price, support_level, market_condition, signal_quality, rs_value 컬럼 추가 + `_migrate()`
- pytest 26개 테스트 작성 (합성 OHLCV 데이터)
- `docs/weinstein_scanner.md` 작성

### v3.0 — 5개 영역 품질 개선 (현재 최신)

#### 1. `weinstein.py` — 시그널 정확도 향상
- **BREAKOUT 강화**
  - 돌파 당일 종가 ≥ 고가 × 0.70 (긴 윗꼬리 봉 제외)
  - 베이스 품질 검증: 직전 10일 중 7일 이상 MA150 ±5% 횡보 → `base_quality=STRONG`
- **REBOUND 강화**
  - close 기준 → **low 기준** 터치 감지로 변경
  - MA150 slope > 0.02 필수 조건 추가
  - 반등 확인일 거래량 ≥ 평균 × 1.3 필수
- **SELL severity 3단계**: HIGH / MEDIUM / LOW
- **리턴 dict 확장**: `ma50`, `base_quality` 필드 추가

#### 2. `kr_stocks.py` — 소형주·저품질 제거
- 키워드 필터: 스팩·SPAC·리츠·ETF·ETN·선물·인버스·레버리지
- 시가총액 ≥ 1,000억 원 (pykrx, 실패 시 graceful skip)
- 종가 ≥ 1,000원

#### 3. `us_stocks.py` — 중복 심볼 제거
- `EXCLUDE_US = {'GOOGL'}` (GOOG와 중복)

#### 4. `market_analysis.py` — 섹터 분석 추가
- US 섹터 ETF 6종 추가: XLK/XLF/XLV/XLE/XLI/XLY
- KR 섹터 ETF 3종 추가: 091160/305720/244580
- `get_market_stages()` 결과에 `US_SECTORS`, `KR_SECTORS` 포함

#### 5. `scan_engine.py` + `models.py` — 등급 시스템
- `_grade(signal) → S/A/B` 함수 추가
- DB `grade` 컬럼 추가 + migration
- 텔레그램 알림 개선:
  - 등급 뱃지: 🔥[S] / ✅[A] / 📌[B]
  - 강세/약세 섹터 한 줄 요약: `📊 강세: 기술, 반도체 | 약세: 에너지`
  - 매도 심각도 아이콘: 🔴 HIGH / 🟠 MEDIUM / 🟡 LOW

#### 테스트
- 26개 → 34개로 증가 (8개 신규: ma50 필드, severity, _grade 로직)

---

## 10. 알려진 제약 / TODO

| 항목 | 상태 | 메모 |
|------|------|------|
| KIS API 연동 | 미완성 | `trading/` 폴더 stub만 존재 |
| DB 경로 하드코딩 | 개선 필요 | `config.py`에 절대경로 박혀 있음 → `DATABASE_URL` env로 이동 권장 |
| 보유주식 현재가 | 수동 업데이트 | KIS API 또는 yfinance 자동 갱신 미구현 |
| 웹 UI 등급 표시 | 미구현 | API에 `grade` 필드 있으나 테이블에 미표시 |
| EXCLUDE_US | 최소화 | 필요시 심볼 추가 가능 |
| pykrx 시가총액 API | 장 종료 후에만 정확 | 장중 스캔 시 전일 기준 적용됨 |

---

## 11. 외부 의존성 요약

| 라이브러리 | 용도 |
|-----------|------|
| `fastapi` + `uvicorn` | 웹 서버 |
| `sqlalchemy` | ORM (SQLite) |
| `apscheduler` | 스케줄 자동 스캔 |
| `yfinance` | 미국 OHLCV + 배치 다운로드 |
| `FinanceDataReader` | NYSE/NASDAQ 종목 목록 |
| `pykrx` | 한국 OHLCV + 시가총액 |
| `pandas` + `numpy` | 데이터 처리 |
| `requests` | KRX API 직접 호출 |
| `python-dotenv` | `.env` 로드 |
| `pytz` | KST 타임존 |
| `pytest` | 단위 테스트 |
