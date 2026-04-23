# Weinstein V2 Implementation Plan

## Current Architecture Summary

- 앱 루트는 `stock-scanner/`이며 FastAPI 서버, SQLite DB, AlpineJS/Tailwind 단일 페이지 UI로 구성되어 있다.
- 스캔 흐름은 `scanner/scan_engine.py`의 `run_scan()`에서 시작해 시장 상태와 벤치마크를 로드한 뒤 KR/US 종목 OHLCV를 순회하고, `scanner/weinstein.py`의 `analyze_stock()`이 매수 시그널을 반환하면 `_save()`가 `ScanResult`에 저장한다.
- `scanner/weinstein.py`에는 이미 주봉 30-SMA, 10-SMA, Mansfield RS, `weekly_stage`, `warning_flags` 등 v2 성격의 계산이 일부 존재한다.
- 현재 DB 모델과 저장 경로는 레거시 일봉 중심 필드에 맞춰져 있으며, `rs_value`에는 legacy ratio RS가 저장되는 구조다.
- `/api/results`와 웹 결과 카드는 `stage`, `ma150`, `volume_ratio` 등 기본 필드만 노출하므로 주봉 Stage와 Mansfield RS를 확인할 수 없다.

## Problem Statement

- `analyze_stock()` 결과에 주봉/v2 필드가 있어도 `_save()`가 대부분 저장하지 않아 Mansfield RS, 주봉 Stage, 주봉 거래량비, 경고 플래그가 유실된다.
- 기존 `rs_value` 컬럼은 상대강도 저장용으로 쓰이고 있어 Mansfield RS를 같은 컬럼에 덮어쓰면 과거 결과 의미가 깨진다.
- DB, API, UI, 문서가 서로 다른 버전 용어와 필드 의미를 사용하고 있어 실제 저장된 신호를 해석하기 어렵다.
- pivot/base 탐지와 rebound 탐지는 no-look-ahead 보장이 중요하지만, v2 기준의 회귀 테스트가 부족하다.

## Goals / Non-Goals

### Goals

- `WEINSTEIN_MODE` 또는 `ENABLE_WEINSTEIN_V2` 설정으로 v2 경로를 명확히 켠다.
- 기존 legacy RS 의미를 유지하고 Mansfield RS는 `mansfield_rs`라는 별도 필드와 컬럼으로 저장한다.
- 주봉 Stage, 30주/10주 SMA, 주봉 거래량비, Mansfield RS, RS 추세, base 기간/폭, warning flags를 DB/API/UI까지 연결한다.
- v2 필드 저장과 조회가 기존 `BREAKOUT`, `RE_BREAKOUT`, `REBOUND` 시그널 저장 흐름과 함께 동작하게 한다.
- pivot/base와 rebound의 false positive 및 no-look-ahead 회귀 테스트를 추가한다.

### Non-Goals

- 전체 앱 재작성, 데이터 수집기 교체, 계좌/거래/보유 기능 변경은 하지 않는다.
- 기존 시그널 타입 이름과 저장 중복 기준은 바꾸지 않는다.
- 실데이터 다운로드 의존 테스트나 백테스트 프레임워크는 이번 범위에 넣지 않는다.
- 기존 DB 컬럼 삭제나 과거 데이터 마이그레이션은 하지 않는다.

## Files To Change

- `scanner/weinstein.py`
  - v2 payload 필드명을 명확히 정리한다.
  - Mansfield RS를 `mansfield_rs`로 노출하고 legacy `rs`는 유지한다.
  - strict v2 모드에서 주봉 Stage2와 Mansfield RS 양수 조건을 hard filter로 적용한다.
- `scanner/scan_engine.py`
  - `_save()`가 v2 필드를 저장하도록 확장한다.
  - `_grade()`는 기본적으로 legacy RS를 유지하고, 승인된 설정에서만 Mansfield RS 정책을 반영한다.
- `database/models.py`
  - `scan_results`에 nullable v2 컬럼을 추가한다.
  - `_migrate()`에 backward-compatible `ALTER TABLE` 항목을 추가한다.
- `config.py`
  - `WEINSTEIN_MODE`, `ENABLE_WEINSTEIN_V2`, v2 strict 필터 관련 설정을 추가한다.
- `web/app.py`
  - `/api/results` 응답에 v2 필드를 포함한다.
- `web/templates/index.html`
  - 결과 카드에 `weekly_stage`, `mansfield_rs`, `warning_flags`를 우선 표시한다.
- `tests/test_weinstein.py`
  - 기존 테스트를 유지하면서 v2 저장, API 응답, 주봉/Mansfield/no-look-ahead 테스트를 추가한다.
- `docs/weinstein_scanner.md`, `PROJECT_OVERVIEW.md`
  - v2 필드 의미, DB 저장 의미, 설정값, API 응답 설명을 최신화한다.

## Design Decisions

- 기본 모드는 `WEINSTEIN_MODE=legacy`로 둔다. 기존 운영 결과와 시그널 수를 갑자기 바꾸지 않기 위함이다.
- `ENABLE_WEINSTEIN_V2=true` 또는 `WEINSTEIN_MODE=v2`일 때 v2 필드 계산과 노출을 명확히 활성화한다.
- `WEINSTEIN_MODE=strict` 또는 v2 strict 설정에서는 주봉 Stage2와 Mansfield RS 양수를 신규 매수 시그널 hard filter로 적용한다.
- `rs_value`는 legacy ratio RS 저장용으로 유지하고, Mansfield RS는 `mansfield_rs`에만 저장한다.
- `warning_flags`는 SQLite 호환성을 위해 JSON 문자열을 저장하고, API/UI에서 파싱 실패 시 빈 목록으로 처리한다.

## DB / Schema Impact

- 기존 컬럼은 삭제하거나 의미를 바꾸지 않는다.
- `scan_results`에 아래 nullable 컬럼을 추가한다.
  - `weekly_stage VARCHAR(10)`
  - `sma30w REAL`
  - `sma10w REAL`
  - `weekly_volume_ratio REAL`
  - `mansfield_rs REAL`
  - `rs_trend VARCHAR(10)`
  - `base_weeks REAL`
  - `base_width_pct REAL`
  - `warning_flags TEXT`
- `_migrate()`에서 컬럼 존재 여부를 확인한 뒤 누락된 컬럼만 `ALTER TABLE`로 추가한다.
- 새 컬럼은 nullable이므로 기존 DB와 과거 row는 그대로 유지된다.

## Test Plan

- 기존 `tests/test_weinstein.py` 전체가 계속 통과해야 한다.
- 주봉 리샘플링 테스트:
  - 일봉 OHLCV가 주봉 Open=첫날, High=max, Low=min, Close=마지막 날, Volume=sum으로 집계되는지 확인한다.
- Stage 분류 테스트:
  - 주봉 30-SMA와 기울기로 Stage2, Stage3, Stage4가 구분되는지 확인한다.
- Mansfield RS 테스트:
  - 벤치마크 대비 강세면 양수, 약세면 음수로 계산되는지 확인한다.
- no-look-ahead 테스트:
  - pivot/base 탐지가 현재 돌파 봉을 base 계산에 포함하지 않는지 확인한다.
  - rebound가 눌림 이후 반등 순서에서만 발생하는지 확인한다.
- 저장/API 테스트:
  - `_save()`가 `rs_value`와 `mansfield_rs`를 서로 다른 컬럼에 저장하는지 확인한다.
  - `/api/results`가 v2 필드를 반환하는지 확인한다.

## Verification Commands

```bash
cd stock-scanner
venv/bin/python -m pytest tests/test_weinstein.py -v
venv/bin/python -m pytest tests/ -v
venv/bin/python -m pytest tests/test_weinstein.py -v --tb=short
```

## Risks / Rollback Notes

- `rs_value` 의미를 바꾸면 과거 결과 해석이 깨지므로 반드시 legacy ratio RS로 유지한다.
- v2 strict 필터를 기본 활성화하면 시그널 수가 줄 수 있으므로 기본값은 backward-compatible legacy 모드로 둔다.
- `warning_flags` JSON 파싱 실패는 UI/API에서 안전하게 빈 목록으로 처리해야 한다.
- 새 DB 컬럼은 nullable이므로 롤백 시 코드에서 무시하면 된다. 컬럼 삭제는 필요하지 않다.
- 구현 중 접근 방식이 크게 바뀌면 이 계획 파일을 먼저 업데이트한 뒤 코드를 수정한다.

## Top 5 Approval Decisions

1. v2 기본값은 `WEINSTEIN_MODE=legacy`로 둔다.
2. 기존 `rs_value`는 legacy ratio RS로 유지하고 Mansfield RS는 `mansfield_rs` 새 컬럼에 저장한다.
3. v2 strict 모드에서만 주봉 Stage2와 Mansfield RS 양수를 hard filter로 적용한다.
4. UI는 우선 `weekly_stage`, `mansfield_rs`, `warning_flags`만 결과 카드에 표시하고 상세 수치는 API에 포함한다.
5. 사용자-facing 문서 명칭은 `Weinstein v2`로 통일한다.
