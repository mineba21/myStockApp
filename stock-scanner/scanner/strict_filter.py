"""Strict Weinstein optimal-buy filter — gate decision functions.

본 모듈은 외부 데이터 fetch 를 일절 하지 않는다. 사전에 계산된 signal dict
+ ctx dict 만 입력으로 받아 거부 사유 리스트(reason enum 문자열) 를
in-place 로 채우거나 (passed: bool, reasons: list[str]) 를 반환한다.

이 분리는 weinstein.py 의 *순수 계산 → DB / 알림 결정* 경로를 깨끗하게
유지하기 위함이다 (CLAUDE.md "Strategy logic should be testable with
synthetic pandas data without calling external data sources").

## Phasing

* **Phase 2 (현재)**: `_check_rs` 와 RS reason 상수만 정의.
  `apply_strict_filter` 엔트리포인트와 다른 게이트(`_check_market`,
  `_check_weekly_stage`, `_check_volume`, `_check_extension`,
  `_check_stop_loss`, `_check_base`, `_check_sector`) 는 Phase 3 에서 추가됨.
* Phase 4 에서 scan_engine.py 가 `apply_strict_filter` 를 호출해
  `signal["strict_filter_passed"]` / `signal["filter_reasons"]` 를 채움.
  Phase 2 머지 시점에는 아직 호출자가 없어 동작 영향 없음.

## Reason 상수 안정성

DB(`scan_results.filter_reasons` JSON) 와 모니터링 대시보드가 이 enum
문자열에 의존하므로, **상수 값을 바꾸면 changelog 의무**.
"""
from typing import Any, Dict, List

from config import (
    STRICT_REQUIRE_RS_POSITIVE,
    STRICT_REQUIRE_RS_RISING,
    STRICT_REQUIRE_RS_ZERO_CROSS_FOR_BREAKOUT,
)


# ── Reason enum 상수 ───────────────────────────────────────────────
# Gate 6 — Mansfield Relative Strength
RS_BELOW_ZERO        = "rs_below_zero"
RS_FALLING           = "rs_falling"
RS_BENCHMARK_MISSING = "rs_benchmark_missing"
RS_NO_ZERO_CROSS     = "rs_no_zero_cross"


def _check_rs(signal: Dict[str, Any],
              ctx: Dict[str, Any],
              reasons: List[str]) -> None:
    """Gate 6 — Mansfield Relative Strength.

    검사 항목:
      1. STRICT_REQUIRE_RS_POSITIVE
         - benchmark 자체 없음 또는 RS 산출 실패 → "rs_benchmark_missing"
         - rs_value < 0                          → "rs_below_zero"
      2. STRICT_REQUIRE_RS_RISING
         - rs_trend == "FALLING"                 → "rs_falling"
      3. STRICT_REQUIRE_RS_ZERO_CROSS_FOR_BREAKOUT (BREAKOUT 만)
         - rs_zero_crossed != True               → "rs_no_zero_cross"

    Args:
        signal: analyze_stock 결과 dict. 필요 키:
                signal_type, rs_value, rs_trend, rs_zero_crossed.
        ctx:    상위 컨텍스트. 필요 키: benchmark_present (bool).
        reasons: in-place 로 fail 사유를 append 받을 리스트.
    """
    sig_type = signal.get("signal_type")

    # 1) RS 양수 강제
    if STRICT_REQUIRE_RS_POSITIVE:
        if not ctx.get("benchmark_present"):
            reasons.append(RS_BENCHMARK_MISSING)
        else:
            rs_value = signal.get("rs_value")
            if rs_value is None:
                # 벤치마크는 있지만 RS 산출 실패 (데이터 부족 등) — 동일 사유로 통일
                reasons.append(RS_BENCHMARK_MISSING)
            elif rs_value < 0.0:
                reasons.append(RS_BELOW_ZERO)

    # 2) RS 상승 추세 강제
    if STRICT_REQUIRE_RS_RISING:
        if signal.get("rs_trend") == "FALLING":
            reasons.append(RS_FALLING)

    # 3) BREAKOUT 한정 — 최근 RS 0선 음→양 전환
    if (STRICT_REQUIRE_RS_ZERO_CROSS_FOR_BREAKOUT
            and sig_type == "BREAKOUT"):
        # rs_zero_crossed 가 None(미산출) 이거나 False 이면 fail
        if not signal.get("rs_zero_crossed"):
            reasons.append(RS_NO_ZERO_CROSS)
