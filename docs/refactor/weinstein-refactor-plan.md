# myStockApp Weinstein 리팩토링 계획

## Status (2026-04-27 기준)

- ✅ **Phase 1 완료** — 전략 코어 강화 + 단위 테스트 보강.
- ✅ **Phase 2 완료** — 스캐너 통합 + KR/US `fetch_ohlcv` 어댑터.
- ✅ **Phase 3 완료** — `GET /api/chart/ohlcv` 차트 데이터 API.
- ✅ **Phase 4 완료** — Lightweight-Charts 기반 차트 UI 모달.
- ✅ **Phase 5 완료** — 본 문서/`docs/strategy/weinstein.md` 동기화 + AGENTS.md 정리.

별도 PR 권장(본 리팩토링 범위 밖)은 본 문서 마지막 섹션 참조.

## Context

myStockApp은 Stan Weinstein의 Stage Analysis를 따르는 KR/US 주식 스캐너(FastAPI + SQLAlchemy + Alpine.js)이다. 본 리팩토링 시작 시점의 main 브랜치 코드는 30주 MA 기반 주봉 지표와 Mansfield RS, base pivot, BREAKOUT/RE_BREAKOUT/REBOUND 매수 시그널, 매도 시그널을 이미 구현하고 있으나 다음 격차가 있었다(Phase 5 완료 후 모두 해소됨).

- **테스트 격차** *(Phase 1에서 해소)*: 주봉 지표(`compute_weekly_indicators`)와 Mansfield RS(`compute_relative_performance`), `detect_base_pivot` 직접 테스트가 없었다.
- **매도 로직** *(Phase 1+2에서 해소)*: `check_sell_signal()`이 일봉 150MA만 보고 30주 MA 붕괴/RS 악화/주봉 슬로프 반전을 직접 보지 않았다.
- **KR/US 페치 비대칭** *(Phase 2에서 해소)*: `get_kr_ohlcv` vs `get_us_ohlcv`의 시그니처(`period_years` vs `period`)와 배치 동작이 다름 → 공통 `fetch_ohlcv(ticker, lookback_days)` 어댑터로 통일.
- **차트 부재** *(Phase 3+4에서 해소)*: `GET /api/chart/ohlcv` + Lightweight-Charts 모달.

본 계획은 **당시 main을 단일 진실 소스**로 두고, 작은 PR 단위로 (1) 전략 코어와 테스트를 강화 → (2) 스캐너 통합 → (3) 차트 데이터 API → (4) 차트 UI → (5) 검증/정리 5단계로 분리했다. 각 단계는 단독 PR로 머지 가능하며, 전 단계가 끝나야 다음 단계 시작.

전략의 "왜"는 [docs/strategy/weinstein.md](../strategy/weinstein.md)를 참조한다. 본 문서는 "어떻게/언제"만 다룬다.

---

## 시작 시점 코드베이스 스냅샷 (감사 결과 요약, 머지 전 기준)

### 1) 프로젝트 구조

```
stock-scanner/
├── main.py                    # FastAPI 앱 부트스트랩
├── scheduler.py               # APScheduler 스케줄링
├── config.py                  # .env 로딩, 임계값(BREAKOUT_*, RS_LOOKBACK_WEEKS 등)
├── scanner/
│   ├── kr_stocks.py           # FDR + pykrx fallback, 시가총액/가격 필터
│   ├── us_stocks.py           # yfinance, S&P500/NASDAQ100/NYSE 유니버스
│   ├── market_analysis.py     # 시장지수 stage 판정 → BEAR/CAUTION/BULL/NEUTRAL
│   ├── scan_engine.py         # run_scan(market) 오케스트레이션
│   └── weinstein.py           # 주봉지표/Mansfield RS/base pivot/3종 매수/매도 시그널
├── database/
│   └── models.py              # SQLAlchemy: scan_results, scan_logs, accounts, transactions, holdings, watchlist
├── notifications/telegram.py
├── web/
│   ├── app.py                 # FastAPI 라우트 (/api/* + /)
│   └── templates/index.html   # Tailwind + Alpine.js, 차트 라이브러리 없음
└── tests/test_weinstein.py    # 합성 데이터로 BREAKOUT/RE_BREAKOUT/REBOUND/SELL/유틸리티 테스트
```

### 2) KR/US 데이터 페치 흐름

- `kr_stocks.get_kr_ohlcv(ticker, period_years=2)` → FDR 1차, pykrx fallback. 컬럼명 한글→영문 정규화. 50일 미만이면 None.
- `kr_stocks.get_all_kr_tickers()` → KRX 공식 API에서 STK+KSQ 목록. 시총 ≥ 1,000억, 가격 ≥ 1,000원, 스팩/ETF/레버리지 제외.
- `us_stocks.get_us_ohlcv(ticker, period="2y")` → yfinance 단건. `get_us_batch()`는 `yf.download` 멀티스레드 50개 묶음.
- `us_stocks.get_all_us_tickers()` → S&P500/NASDAQ100 위키 파싱 + FDR `StockListing`.
- 외부 실패는 대체로 `None`/`[]` 반환으로 graceful 처리. 단, **timeout과 빈데이터 구분이 모호**.

### 3) Weinstein 코어 (scanner/weinstein.py)

- `to_weekly_ohlcv(daily_df)` → 일봉을 주봉(W-FRI)으로 리샘플.
- `compute_weekly_indicators(weekly_df)` → 30주/10주 SMA, slope.
- `classify_stage(close, sma30w, slope30w)` → STAGE1/2/3/4.
- `compute_relative_performance(stock_close, benchmark_close)` → Mansfield RS(52주 SMA 기준), 5주 기울기.
- `detect_base_pivot(weekly_df)` → 5~26주 횡보, 폭 ≤15%(TIGHT≤8%), 가장 최근 base 1개.
- `analyze_stock(ticker, daily_df, market, benchmark_close, market_condition)` →
  - BREAKOUT (Stage1→2 + base pivot 돌파 + 거래량)
  - RE_BREAKOUT (Stage2 진행 중 continuation base 돌파)
  - REBOUND (Stage2 + MA50 지지 반등, 거래량 1.3x)
- `check_sell_signal()` → 손절가/MA150 하향/MA150 슬로프 반전/Stage3 진입 (HIGH/MED/LOW).
- 일봉 150MA는 legacy 호환 경로(`_build_indicators`)에서 사용.

### 4) 스캔 오케스트레이션 (scanner/scan_engine.py)

1. `get_market_stages()` → 미국/한국 지수 stage → 시장 condition.
2. SPY/069500 Close 시리즈 로드(RS 벤치마크).
3. `_scan_kr()` / `_scan_us()` → 유니버스 → OHLCV → `analyze_stock` → 결과 → DB 저장.
4. `_check_watchlist()` → 보유/감시 종목 매도 시그널.
5. `_notify()` → 텔레그램.
6. `ScanLog` 상태 갱신 + 에러 캡처.

### 5) 웹/API/템플릿 (web/app.py, web/templates/index.html)

- FastAPI + Pydantic + Jinja2.
- 라우트(요약): `GET /`, `POST /api/scan/start`, `GET /api/scan/status`, `GET /api/results` (필터: market, signal_type, days), 결과 삭제, `GET /api/scan/logs`, accounts/transactions/holdings/watchlist CRUD, `GET /api/telegram/test`, `GET /api/market/status`, `GET /api/exchange-rate`, `GET /api/settings`.
- 프론트엔드: Tailwind + Alpine.js CDN. Jinja 템플릿 1개(index.html). **차트 라이브러리 미사용.**
- DB: SQLAlchemy 1.4, 수동 `_migrate()` ALTER TABLE.

### 6) 차트 데이터 추가 자연스러운 위치

- 백엔드: `web/app.py`의 GET 엔드포인트 그룹에 `GET /api/chart/ohlcv` 추가. 입력: `market`, `ticker`, `timeframe(daily|weekly)`, `lookback_days`.
- 데이터 소스: 기존 `get_kr_ohlcv` / `get_us_ohlcv` 재사용. 주봉은 `weinstein.to_weekly_ohlcv()` 재사용.
- 프론트: `index.html` 결과 테이블 행에 차트 버튼/클릭 → 모달. 차트 라이브러리는 **Lightweight-Charts CDN** 우선 검토(가볍고 OHLCV에 최적). 차선: Chart.js. 새 번들러는 도입하지 않음(CLAUDE.md 규칙).

### 7) 테스트 현황

- `tests/test_weinstein.py` 1개 파일. 합성 pandas 데이터(`_make_df`, `_make_stage2_base`).
- **존재**: BREAKOUT 4개, RE_BREAKOUT 3개, REBOUND 5개, market filter 5개, SELL 3개, 유틸리티 5개.
- **부재(High)**: `compute_weekly_indicators`, `compute_relative_performance`(Mansfield RS), `detect_base_pivot` 직접 단위 테스트.
- **부재(Med)**: STAGE3 경계, 주봉 슬로프 반전 매도 셀, KR/US 페치 어댑터의 결정적 입력 테스트, 차트 API 응답 스키마 테스트.

---

## 단계별 PR 구획

> **공통 규칙**: 각 PR은 단독으로 머지 가능. 단계 시작 전 `python -m compileall scanner database web` + `pytest -q` 그린 확인. CLAUDE.md "Expected Completion Summary" 형식으로 요약.

### ✅ Phase 1 (완료): 전략 코어 강화 + 단위 테스트 (매도 로직은 추가만, 호출부 미변경)

**목표**: 누락된 단위 테스트 채우고, `check_sell_signal`을 30주 MA/RS 기반으로 보강하되 **기존 시그니처와 기본 호출 결과를 유지**.

**파일 변경**:

- `scanner/weinstein.py`
  - 미사용 import(sys, os) 제거.
  - `check_sell_signal(...)`에 옵션 인자 `weekly_df=None`, `benchmark_close=None` 추가(기본값 유지 → 기존 호출부 영향 없음).
  - 옵션 인자가 주어졌을 때:
    - 주봉 30MA 하향 이탈 시 HIGH 추가.
    - 30주 MA 슬로프 양→음 반전 시 MEDIUM 추가.
    - Mansfield RS 5주 기울기 음전환 + RS<0 시 MEDIUM 추가.
  - 새 helper(필요 시): `_weekly_breakdown(weekly_df)`, `_rs_deteriorating(stock_close, benchmark_close)`.
- `tests/test_weinstein.py`
  - `compute_weekly_indicators` 단위 테스트 3+ (정상, 결측치, 짧은 시리즈).
  - `compute_relative_performance` 단위 테스트 3+ (RS 0 부근, 음수, 5주 기울기 부호).
  - `detect_base_pivot` 단위 테스트 3+ (TIGHT, LOOSE, base 미존재).
  - `check_sell_signal` 신규 분기 테스트 3+ (주봉 30MA breakdown, 슬로프 반전, RS 악화).
  - STAGE3 경계 테스트 1+.

**기대 동작**:

- 기존 BREAKOUT/RE_BREAKOUT/REBOUND 결과 동일.
- 호출부에서 weekly/benchmark을 안 넘기면 기존 매도 셀 결과 동일.
- 새 분기는 옵션 인자가 들어왔을 때만 발현.

**검증**:

```bash
python -m compileall scanner database
pytest -q tests/test_weinstein.py
```

- 전체 합성 데이터 테스트 그린.
- 기존 통과한 테스트 회귀 0건.

**리스크**:

- 옵션 인자를 잘못 디폴트하면 호출부 회귀. → 기본값 None 유지로 가드.
- Mansfield RS는 52주 데이터를 요구 → 짧은 시리즈에서는 None 반환하도록 처리.

---

### ✅ Phase 2 (완료): 스캐너 통합 (호출부에서 옵션 인자 채우기 + 페치 시그니처 통일)

**목표**: Phase 1에서 만든 옵션 매도 분기를 `scan_engine`이 실제로 활용하도록 호출부 결선. KR/US OHLCV 페치 시그니처를 정합화하되 기존 호출부는 그대로 동작.

**파일 변경**:

- `scanner/scan_engine.py`
  - `_check_watchlist()`에서 `check_sell_signal(... weekly_df=to_weekly_ohlcv(daily_df), benchmark_close=bench_close)` 형태로 인자 추가.
  - `analyze_stock` 호출 시 `benchmark_close`가 이미 전달됨 → 변경 없음.
- `scanner/kr_stocks.py`, `scanner/us_stocks.py`
  - 새 통합 어댑터 `fetch_ohlcv(market: str, ticker: str, lookback_days: int = 730) -> Optional[pd.DataFrame]` 추가(기존 `get_kr_ohlcv`/`get_us_ohlcv` 호출). **기존 함수 시그니처는 유지**.
  - 미국 `period="2y"` 와 한국 `period_years=2`는 그대로 두고, 신규 어댑터에서만 days 단위로 변환.
- `tests/test_scan_engine.py` (신규, 작게)
  - `_check_watchlist`가 매도 분기 옵션 인자를 채워 호출하는지 monkeypatch로 확인.
  - `fetch_ohlcv` 어댑터가 KR/US를 올바른 함수로 라우팅하는지 monkeypatch로 확인(외부 호출 없이).

**기대 동작**:

- 시장 BEAR + Stage4 차단 정책 유지.
- 매도 시그널이 더 일찍 발생할 수 있음(주봉/RS 기반). 알림은 기존 텔레그램 포맷 그대로.
- `fetch_ohlcv` 신규 함수는 Phase 3에서 차트 API가 사용 예정.

**검증**:

```bash
python -m compileall scanner database
pytest -q
# 수동: 로컬에서 main.py 실행, /api/scan/start 1회, /api/results 폴링.
```

**리스크**:

- `_check_watchlist` 호출 시 daily_df가 None이면 옵션 인자 None으로 폴백 보장.
- 새 어댑터를 어디서도 사용하지 않으면 dead code → Phase 3 머지 전까지 어댑터를 export만 하고 호출은 차트 API에서.

---

### ✅ Phase 3 (완료): 차트 데이터 API

**목표**: 결과 행에서 일봉/주봉 OHLCV를 JSON으로 받아갈 수 있는 GET 엔드포인트 1개.

**파일 변경 (실제 머지된 형태)**:

- `web/app.py`
  - 신규 라우트: `GET /api/chart/ohlcv`
  - Pydantic 검증(쿼리): `market: Literal["KR","US"]`, `ticker: str`(영숫자/대시 정규식, case-insensitive), `timeframe: Literal["daily","weekly"] = "daily"`, `range: Literal["6m","1y","2y","5y"] = "1y"`.
  - 데이터 소스: `scanner.kr_stocks.fetch_ohlcv` or `scanner.us_stocks.fetch_ohlcv`(Phase 2의 어댑터). 페치 시 MA 계산용 buffer(150일/30주분 + 여유) 추가 후 마지막에 visible window로 trim.
  - timeframe=weekly이면 `scanner.weinstein.to_weekly_ohlcv` 적용. `ma_period`는 daily=150, weekly=30.
  - 응답: `{"market":..., "ticker":..., "timeframe":..., "range":..., "ma_period":150|30, "candles":[{"t":"YYYY-MM-DD","o":..,"h":..,"l":..,"c":..,"v":..,"ma":...|null}, ...]}`. 빈 데이터는 `candles: []` + 200 OK.
  - 실패: 외부 페치 에러는 503, 입력 검증 실패는 422.
- `tests/test_chart_api.py` (신규)
  - 입력 검증 케이스(market/timeframe/range 잘못, ticker 특수문자 등) → 422.
  - 빈 데이터 케이스(monkeypatch fetch_ohlcv → None / 빈 DF) → candles=[] + 200.
  - 정상 케이스(monkeypatch fetch_ohlcv → 합성 DataFrame) → JSON 형태/길이/MA 검증.
  - timeframe=weekly에서 to_weekly_ohlcv 호출 확인.
  - KR/US 라우팅 회귀.

**기대 동작**:

- UI 없이도 curl/`pytest`로 JSON 응답 확인 가능.
- 외부 데이터 소스 호출은 라우트에서만 발생(전략 코어 청결 유지).

**검증**:

```bash
python -m compileall scanner database web
pytest -q tests/test_chart_api.py
# 수동: uvicorn 띄우고 curl 'http://127.0.0.1:8000/api/chart/ohlcv?market=KR&ticker=005930&timeframe=daily&lookback_days=365'
```

**리스크**:

- yfinance/FDR 호출 비용 → 차트 API에서 캐시는 도입하지 않음(요청당 페치, 추후 단계에서 재고). DB OHLCV 캐시는 본 리팩토링 범위 밖(별도 PR 권장).
- 잘못된 ticker로 외부 호출 폭주 → 입력 검증 정규식 + 단순 rate limit는 추후.

---

### ✅ Phase 4 (완료): 차트 UI (스캔 결과에서 모달 차트 열기)

**목표**: 결과 테이블 행에서 차트 버튼 클릭 → 모달 → 일봉/주봉 토글 + 거래량.

**파일 변경 (실제 머지된 형태)**:

- `web/templates/index.html`
  - `<head>`에 Lightweight-Charts v4.2.0 standalone CDN 1줄 추가(번들러 미도입 — CLAUDE.md 규칙 준수).
  - 결과 카드 액션 영역에 `📈 차트` 버튼 추가(`+ 매수`, `👁 감시` 옆). Alpine.js 상태 `chartModal = { open, market, ticker, name, timeframe, range, loading, error, empty }`.
  - 모달 내 `<div id="chart-container">` + 일봉/주봉 토글 + range select(6m/1y/2y/5y). 거래량은 별도 pane이 아니라 메인 차트의 histogram series로 통합(Lightweight-Charts v4 패턴: `priceScaleId:"volume"` + `scaleMargins`).
  - MA line(150 또는 30) 오버레이는 백엔드가 보낸 `ma` 필드를 그대로 line series로 그림.
  - JS 함수 `openChart(r)`/`closeChart()`/`setChartTimeframe(tf)`/`setChartRange(r)`/`loadChart()` → `fetch('/api/chart/ohlcv?...')` → Lightweight-Charts 시리즈 갱신.
  - 로딩 스피너 / "📭 데이터 없음" / "⚠️ 차트를 불러오지 못했습니다" + 다시 시도 버튼. ESC와 backdrop 클릭으로 닫기. ResizeObserver로 가변 폭 대응.
- 변경 없음: `web/app.py`(Phase 3에서 끝남), `scanner/*`.

**기대 동작**:

- 스캔 결과 행 클릭 → 모달 → 일봉 차트 즉시 로드. 토글 시 주봉 차트 로드. 모달 외부 클릭/ESC로 닫기.
- 모바일 폭에서도 모달 폭/높이 적절(Tailwind 반응형 클래스).

**검증**:

- 자동: 새 JS는 단위 테스트 없이 두되, `tests/test_chart_api.py`가 백엔드 응답 보장.
- 수동: `bash start_server.sh` (또는 `uvicorn main:app`) → 브라우저에서:
  - 스캔 1회 실행 → 결과 테이블 → 차트 버튼 → 모달.
  - daily/weekly 토글, 빈 ticker 시 "데이터 없음", 네트워크 차단 시 에러 메시지.
- `python -m compileall web` (템플릿은 영향 없음).

**리스크**:

- CDN 가용성 → Lightweight-Charts CDN 다운 시 차트 로딩 실패. 대안 CDN 또는 latest 핀 버전.
- 모달이 큰 결과셋에서 행 이벤트 위임 못 하면 메모리 누수. → Alpine `x-on:click` 위임 패턴 확인.

---

### ✅ Phase 5 (완료): 최종 검증 및 정리

**실제 수행한 작업**:

- `docs/strategy/weinstein.md`: 매도 분기(주봉 30MA 붕괴/슬로프 반전/RS 악화/일봉 MA150 슬로프/Stage3 진입/손절가) 명시 + 코드 책임 분리에 차트 layer 추가.
- `docs/refactor/weinstein-refactor-plan.md`(본 문서): Phase 1~5 완료 마킹, 시작 시점 스냅샷 라벨 명확화, Phase 3/4 실제 응답 스키마와 UI 형태로 갱신.
- `AGENTS.md`: 존재하지 않는 obsolete 경로(`stock-scanner/PROJECT_OVERVIEW.md`, `stock-scanner/docs/weinstein_scanner.md`)를 현 docs(`docs/strategy/weinstein.md`, `docs/refactor/weinstein-refactor-plan.md`)로 교체.
- `requirements.txt`: 신규 의존성 추가 없음 확인(차트는 CDN, 백엔드는 기존 fastapi/pandas/yfinance/FDR 그대로).
- `scanner/weinstein.py`: `_build_indicators` 사용처 점검(legacy 호환 경로지만 현행 단위 테스트와 `analyze_stock` 내부 호출에서 활발히 쓰임 → 제거 NO, 시그니처 그대로 유지).
- 코드 변경 없음: 전략·스캐너·API·UI는 Phase 1~4 머지 결과 그대로.

**검증 명령**:

```bash
python -m compileall scanner database web
pytest -q
# 수동 종단(가능하면): uvicorn main:app → 결과 카드 → 차트 모달 → daily/weekly/range 토글.
```

**남은 리스크 / Known limitations**:

- 차트 라이브러리(Lightweight-Charts) CDN 의존. 오프라인/CDN 장애 시 모달은 명시적 에러 표시.
- 데이터 소스 외부 변동(yfinance/FDR API 변경)은 본 리팩토링 범위 밖이지만, 회귀 시 fail-soft.
- `web/app.py`의 `index()` 핸들러가 `templates.TemplateResponse("index.html", {"request": request})` 옛 시그니처를 사용 → 일부 신규 Starlette 환경에서 500. 별도 PR로 keyword-arg 형식으로 교체 권장(본 PR 스코프 외).
- `config.DATABASE_URL`이 `/Users/mac/.stock_scanner.db`로 하드코딩. 사용자별 머신에서 boot 실패 → 별도 PR에서 env 기반으로 추출 권장.
- 추후 작업으로 분리 권장: OHLCV DB 캐시, 섹터 stage 판정 모듈, 스케줄러 BEAR 자동 일시정지, 차트 API 캐시/rate limit.

---

## 핵심 파일 (수정 가능성 큰 순서)

- `scanner/weinstein.py` — Phase 1, Phase 5
- `tests/test_weinstein.py` — Phase 1
- `scanner/scan_engine.py` — Phase 2
- `scanner/kr_stocks.py`, `scanner/us_stocks.py` — Phase 2 (어댑터 추가만)
- `web/app.py` — Phase 3
- `web/templates/index.html` — Phase 4
- `tests/test_scan_engine.py`, `tests/test_chart_api.py` — Phase 2/3 신규

## 재사용할 기존 함수

- `scanner.weinstein.to_weekly_ohlcv` — 차트 API 주봉 변환에 그대로 사용.
- `scanner.weinstein.compute_weekly_indicators`, `compute_relative_performance`, `detect_base_pivot` — 매도 보강 분기에서 호출.
- `scanner.kr_stocks.get_kr_ohlcv`, `scanner.us_stocks.get_us_ohlcv` — 차트 API 백엔드 데이터 소스(어댑터를 통해).
- `scanner.market_analysis.get_market_stages` — 시장 condition 그대로.
- `database.models.ScanResult` — 차트 모달이 표시할 row의 ticker/market 그대로 활용.

## 종단 검증 시나리오 (Phase 5에서 한 번)

1. `python -m compileall scanner database web` 그린.
2. `pytest -q` 그린(전체).
3. `bash start_server.sh` → `http://127.0.0.1:8000/`:
   - `POST /api/scan/start` (KR 또는 US) → `GET /api/scan/status` polling → `GET /api/results` 응답.
   - 결과 행에서 차트 버튼 클릭 → 모달 → daily 정상 → weekly 토글 정상 → ESC 닫기.
   - `GET /api/chart/ohlcv?market=US&ticker=AAPL&timeframe=weekly&lookback_days=730` curl 200 OK.
   - `/api/telegram/test` 200(설정 시).
4. CLAUDE.md "Expected Completion Summary" 형식으로 PR 본문 작성.

## 본 계획에서 제외(별도 PR로 분리 권장)

- OHLCV DB 캐시 도입(스키마 추가).
- 섹터 stage 판정 모듈 신설.
- 스케줄러 BEAR 자동 일시정지.
- 차트 API rate limit/캐시 헤더.
- 새 프론트엔드 빌드 시스템(번들러) — CLAUDE.md 규칙 위반이라 도입 금지.
