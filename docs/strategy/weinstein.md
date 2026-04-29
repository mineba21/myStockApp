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

- 돌파일 거래량은 직전 평균(주봉 또는 일봉) 대비 의미 있게 커야 한다(가능하면 ≥2x).
- 거래량 미달이면 fakeout 위험으로 신호 강도를 낮추거나 차단한다.

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


# docs/strategy/weinstein.md Strict Policy Patch

Add or replace the relevant section in `docs/strategy/weinstein.md` with the following policy.

---

## 엄격 최적 매수 필터 정책

운영 모드가 `STRICT_WEINSTEIN_MODE=true`일 때, BUY 신호는 후보 점수가 아니라 하드 필터 결과다.

다음 조건 중 하나라도 실패하면 BUY 신호를 저장, 알림, 표시하지 않는다.

- 시장이 BEAR 또는 확인 불가
- 섹터가 Stage 2가 아님
- 섹터 확인이 필요한데 섹터가 UNKNOWN
- 종목 주봉 종가가 30주 SMA 아래
- 30주 SMA 기울기가 하락 중
- Stage 3 또는 Stage 4
- 유효한 base/pivot이 없음
- 돌파 거래량이 기준 미달
- Mansfield RS가 0 미만
- Mansfield RS 추세가 하락
- BREAKOUT에서 최근 RS 0선 돌파가 없음
- 가격이 과도하게 이격
- 사전 손절선 산출 불가

`warning_flags`는 보조 진단용이며, 필수 조건 실패를 대체할 수 없다.

---

## 엄격 BUY 체크리스트

엄격 BUY는 아래 항목이 모두 `Yes`일 때만 성립한다.

1. 전체 시장이 Stage 2 또는 최소한 Stage 4가 아닌가?
2. 해당 섹터가 Stage 2인가?
3. 종목이 주봉 30-SMA 위에 있고 30-SMA가 상승 또는 비하락 상태인가?
4. 장기간 base/pivot 저항을 상향 돌파했는가, 또는 Stage 2 내 유효한 pullback/retest인가?
5. 돌파 주봉 거래량이 최근 평균 대비 최소 2배 이상인가?
6. Mansfield RS가 0 이상이고 상승 또는 비하락 상태인가?
7. BREAKOUT의 경우 최근 RS 0선 상향 돌파 또는 명확한 양전환이 있었는가?
8. 가격이 30주선/150일선 대비 과도하게 이격되지 않았는가?
9. 사전 손절선이 명확히 산출되었는가?

이 체크리스트 중 하나라도 실패하면 엄격 BUY가 아니다.
