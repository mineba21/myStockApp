"""Strict Weinstein optimal-buy filter — gate decision unit tests.

Phase 2 scope: RS gate (`scanner.strict_filter._check_rs`) 만 검증.
다른 게이트와 `apply_strict_filter` 엔트리포인트는 Phase 3 에서 추가됨.

설계 원칙:
- 외부 데이터 fetch / DB 접근 없음 — 순수 dict 입력만 사용.
- monkeypatch 로 STRICT_* 플래그를 *명시적* 으로 set 하여 기본값 변경에
  영향받지 않게 한다 (CLAUDE.md "Strategy invariants").
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


# ── 헬퍼 ──────────────────────────────────────────────────────────

def _force_rs_strict_flags(monkeypatch, *,
                           require_positive: bool = True,
                           require_rising:   bool = True,
                           require_zero_cross_for_breakout: bool = True):
    """strict_filter 모듈이 import 시 캡처한 config 플래그를 명시적으로 강제."""
    from scanner import strict_filter
    monkeypatch.setattr(strict_filter, "STRICT_REQUIRE_RS_POSITIVE",
                        require_positive, raising=False)
    monkeypatch.setattr(strict_filter, "STRICT_REQUIRE_RS_RISING",
                        require_rising, raising=False)
    monkeypatch.setattr(strict_filter, "STRICT_REQUIRE_RS_ZERO_CROSS_FOR_BREAKOUT",
                        require_zero_cross_for_breakout, raising=False)


def _passing_breakout_signal(**overrides):
    """RS gate 를 모두 통과하는 baseline BREAKOUT 시그널."""
    sig = {
        "signal_type":     "BREAKOUT",
        "rs_value":        4.5,
        "rs_trend":        "RISING",
        "rs_zero_crossed": True,
    }
    sig.update(overrides)
    return sig


# ══════════════════════════════════════════════════════════════════
# Gate 6 — Mansfield Relative Strength
# ══════════════════════════════════════════════════════════════════

class TestRSGate:
    def test_rs_below_zero_blocks(self, monkeypatch):
        """rs_value < 0 + STRICT_REQUIRE_RS_POSITIVE → 'rs_below_zero'."""
        from scanner.strict_filter import _check_rs, RS_BELOW_ZERO
        _force_rs_strict_flags(monkeypatch)

        sig = _passing_breakout_signal(rs_value=-1.0)
        ctx = {"benchmark_present": True}
        reasons = []
        _check_rs(sig, ctx, reasons)

        assert RS_BELOW_ZERO in reasons

    def test_rs_falling_blocks(self, monkeypatch):
        """rs_trend == 'FALLING' + STRICT_REQUIRE_RS_RISING → 'rs_falling'."""
        from scanner.strict_filter import _check_rs, RS_FALLING
        _force_rs_strict_flags(monkeypatch)

        sig = _passing_breakout_signal(rs_trend="FALLING")
        ctx = {"benchmark_present": True}
        reasons = []
        _check_rs(sig, ctx, reasons)

        assert RS_FALLING in reasons

    def test_no_benchmark_blocks(self, monkeypatch):
        """벤치마크 자체가 없으면 (benchmark_present=False) → 'rs_benchmark_missing'.

        CLAUDE.md spec: 시장 RS 비교가 불가능하면 strict 매수 불허.
        """
        from scanner.strict_filter import _check_rs, RS_BENCHMARK_MISSING
        _force_rs_strict_flags(monkeypatch)

        sig = _passing_breakout_signal(rs_value=None, rs_zero_crossed=False)
        ctx = {"benchmark_present": False}
        reasons = []
        _check_rs(sig, ctx, reasons)

        assert RS_BENCHMARK_MISSING in reasons

    def test_no_zero_cross_blocks_breakout(self, monkeypatch):
        """BREAKOUT + rs_zero_crossed != True → 'rs_no_zero_cross'.

        CLAUDE.md: BREAKOUT 의 strict 조건은 *최근* RS 가 0선 위로 올라온 흔적.
        """
        from scanner.strict_filter import _check_rs, RS_NO_ZERO_CROSS
        _force_rs_strict_flags(monkeypatch)

        sig = _passing_breakout_signal(rs_zero_crossed=False)
        ctx = {"benchmark_present": True}
        reasons = []
        _check_rs(sig, ctx, reasons)

        assert RS_NO_ZERO_CROSS in reasons

        # None 도 동일 — 미산출은 fail
        sig2 = _passing_breakout_signal(rs_zero_crossed=None)
        reasons2 = []
        _check_rs(sig2, ctx, reasons2)
        assert RS_NO_ZERO_CROSS in reasons2

    def test_rebound_does_not_require_zero_cross(self, monkeypatch):
        """REBOUND 은 RS zero-cross 강제 게이트 대상 아님 (BREAKOUT 한정)."""
        from scanner.strict_filter import _check_rs, RS_NO_ZERO_CROSS
        _force_rs_strict_flags(monkeypatch)

        sig = _passing_breakout_signal(signal_type="REBOUND",
                                       rs_zero_crossed=False)
        ctx = {"benchmark_present": True}
        reasons = []
        _check_rs(sig, ctx, reasons)

        assert RS_NO_ZERO_CROSS not in reasons

    def test_all_rs_gates_pass(self, monkeypatch):
        """모든 RS 조건 만족 → reasons 변화 없음."""
        from scanner.strict_filter import _check_rs
        _force_rs_strict_flags(monkeypatch)

        sig = _passing_breakout_signal()
        ctx = {"benchmark_present": True}
        reasons = []
        _check_rs(sig, ctx, reasons)

        assert reasons == []


# ── 실행 ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import subprocess
    subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v", "--tb=short"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
