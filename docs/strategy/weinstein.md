# Stan Weinstein Stage Analysis (myStockApp 채택본)

본 문서는 myStockApp 스캐너가 따르는 Weinstein 전략의 **불변 규칙(Invariants)** 과
**구현 정책(Implementation Policy)** 을 정리한다. 코드는 이 규칙을 깨면 안 된다.

본 문서는 전략의 "왜"를 다룬다. 임계값/매직 넘버 같은 튜닝 파라미터는 `config.py`,
구현 세부는 `scanner/weinstein.py` 코드를 참조하라. 두 곳에 같은 사실을 중복으로 적지 않는다.

## 1. 4단계 분류

- **Stage 1 (Basing)**: 30주 MA가 평탄. 가격이 30주 MA 근처에서 횡보. 거래량 감소.
- **Stage 2 (Advancing)**: 가격 > 30주 MA, 30주 MA 기울기 > 0. 거래량 동반 상승.
- **Stage 3 (Topping)**: 가격이 30주 MA 위에서 분배. 30주 MA 기울기 평탄/둔화.
- **Stage 4 (Declining)**: 가격 < 30주 MA, 30주 MA 기울기 < 0.

## 2. 기준 지표

- **개념적 기반**: 30주 단순이동평균(주봉 종가 기준).
- **근사 허용**: 일봉 150MA는 30주 MA의 근사로만 사용. 가능하면 주봉 30MA를 1차로.
- **필수 부가**: 주봉 10MA(추세 가속), 주봉 종가 슬로프, Mansfield 상대강도(52주).

## 3. 매수 시그널 우선순위

1. **이상적 매수: Stage 1 → Stage 2 BREAKOUT**
   - 의미 있는 base/저항(최소 5주 횡보, 폭 ≤15%) 상향 돌파.
   - 거래량은 최근 평균 대비 의미 있게 증가, 이상적으로 2배 이상.
2. **재돌파(RE_BREAKOUT)**: Stage 2 진행 중 continuation base 상향 돌파.
3. **눌림 후 반등(Pullback Buy)**: 돌파 지지/주요 MA(주로 MA50/30주 MA) 재테스트 후 반등.
4. **MA50 단독 반등은 1차 시그널이 아니다.** 보조 시그널로만 사용한다.

## 4. 거래량

- 돌파일 거래량은 직전 평균(주봉 또는 일봉) 대비 의미 있게 커야 한다.
- 구현상 임계값(`config.py`):
  - 일봉 ≥ `BREAKOUT_DAILY_VOL_RATIO` (기본 3.0x)
  - 주봉 ≥ `BREAKOUT_WEEKLY_VOL_RATIO` (기본 2.0x)
- **Strict 모드에서 거래량 미달은 hard-block** — `weinstein.detect_stage2_breakout`
  가 시그널 자체를 발생시키지 않으며, 추가로 `strict_filter` Gate 5
  (`breakout_daily_volume` / `breakout_weekly_volume`)가 sanity 재검증한다.
  신호 강도 강등(warning_flag) 으로 우회되지 않는다.

## 5. 상대강도(Mansfield RS)

- `RS_raw = stock / benchmark`, `Mansfield = (RS_raw / SMA(RS_raw, 52주) − 1) × 100`.
- 한국: KOSPI200(069500), 미국: SPY를 벤치마크로 사용.
- RS와 RS 추세(예: 5주 기울기)는 종목 품질/필터링에 영향을 줘야 한다.

## 6. 시장/섹터 우선 판정

- 개별 종목 진입 전 시장 stage(BEAR/CAUTION/BULL/NEUTRAL) 확인.
- BEAR에서는 신규 매수 차단 또는 강하게 제한.
- 섹터 stage 체크는 향후 확장 항목으로 명시(현재 미구현, 시장 단계만 적용).

## 7. 매수 차단 규칙

- 시장이 STAGE4 + BEAR이면 신규 매수 시그널 발생 안 함.
- 종목이 STAGE3/STAGE4면 BREAKOUT/RE_BREAKOUT 무효.
- 과매수(예: 30주 MA 대비 과도한 이격) 시 신호 강도 하향.

`STRICT_WEINSTEIN_MODE=True` (기본) 에서는 위 차단 규칙이 8개 게이트로
강화되어 hard-block 으로 작동한다. 거부 사유는 `scan_results.filter_reasons`
(JSON) 으로 추적되며, 실패 게이트는 절대 `warning_flags` 로 강등되지 않는다.
세부: `docs/plans/strict-weinstein-optimal-buy-filter.md`,
`scanner/strict_filter.py`.

## 8. 매도 시그널

다음 중 하나라도 트리거되면 매도 후보:

- **HIGH 우선** 사유:
  - 사전 정의 손절가(stop loss) 도달.
  - 주봉 30MA 하향 이탈(주봉 종가 기준).
  - 일봉 150MA 하향 이탈(보조; 주봉 30MA가 가용하면 우선).
- **MEDIUM** 사유:
  - 30주 MA(또는 일봉 150MA) 기울기 양 → 음으로 반전.
  - Mansfield RS 추세 악화(5주 기울기 음전환 + RS<0).
  - Stage3 진입(가격은 30주 MA 위지만 슬로프가 평탄/둔화).
- **LOW/관찰** 사유:
  - 의미 있는 지지/이전 베이스 하단 근접.
  - 30주 MA 대비 과도한 이격(과매수 후 분배 의심).

매도 분기는 호출부에서 `weekly_df` / `benchmark_close`를 옵션으로 넘겼을 때만 발현하며, 둘 다 없으면 일봉 150MA 기반 legacy 분기만 사용한다. 일봉 150MA는 30주 MA의 근사일 뿐이라는 invariant는 그대로 유지된다.

## 9. 코드 구조 책임 분리

- `scanner/weinstein.py`: 순수 함수만. 외부 데이터 호출 금지.
- `scanner/kr_stocks.py`, `scanner/us_stocks.py`: OHLCV/유니버스 페치만. 공용 어댑터 `fetch_ohlcv(ticker, lookback_days)` 노출.
- `scanner/scan_engine.py`: 오케스트레이션.
- `database/models.py`: 영속성.
- `web/app.py`: HTTP/JSON. 전략 계산은 import만. 차트 데이터는 `GET /api/chart/ohlcv`로 on-demand 제공(스캔 결과에 차트 데이터 동봉 금지).
- `web/templates/index.html`: 렌더링. Lightweight-Charts CDN 사용, 번들러 도입 금지.

## 10. 테스트 정책

- 전략 함수는 합성 pandas 데이터로 단위 테스트 가능해야 한다.
- 외부 데이터 라이브러리(yfinance, pykrx, FDR)에 의존하는 테스트는 통합 테스트로 분리.
