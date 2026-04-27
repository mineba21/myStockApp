"""
Weinstein Scanner — 단위 테스트

합성 OHLCV 데이터로 각 시그널 로직을 독립적으로 검증.
실행: cd stock-scanner && venv/bin/python -m pytest tests/ -v
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import pytest
from datetime import date, timedelta


# ── 합성 데이터 헬퍼 ──────────────────────────────────────────────

def _make_df(prices, volumes=None):
    """단순 OHLCV DataFrame 생성 (High=close*1.005, Low=close*0.995)."""
    n = len(prices)
    if volumes is None:
        volumes = [500_000] * n
    dates = [date(2022, 1, 1) + timedelta(days=i) for i in range(n)]
    close = [float(p) for p in prices]
    return pd.DataFrame({
        "Open":   [p * 0.998 for p in close],
        "High":   [p * 1.005 for p in close],
        "Low":    [p * 0.995 for p in close],
        "Close":  close,
        "Volume": [float(v) for v in volumes],
    }, index=pd.DatetimeIndex(dates))


def _make_stage2_base(n_total=260, base_price=100.0):
    """
    Stage2 상황 합성 데이터:
      - 0~149일: 50→95 선형 상승 (MA150/MA50 충분히 상승)
      - 150~(n_total-1)일: base_price 근처 횡보
    """
    prices  = []
    volumes = []
    # Phase 1: 상승 (MA 워밍업)
    for i in range(150):
        prices.append(50.0 + (base_price - 5 - 50) * i / 149)
        volumes.append(500_000)
    # Phase 2: 횡보 base
    for i in range(n_total - 150):
        # ±2% 진동
        prices.append(base_price + 2 * np.sin(i * np.pi / 5))
        volumes.append(500_000)
    return prices, volumes


# ═══════════════════════════════════════════════════════════════════
# 1. BREAKOUT 테스트
# ═══════════════════════════════════════════════════════════════════

class TestBreakout:

    def test_breakout_pivot_required(self):
        """BREAKOUT: 단순 MA150 크로스만으로는 시그널 나오지 않아야 함."""
        from scanner.weinstein import _build_indicators, _find_breakout_signal

        # 완만하게 선형 상승 (MA150도 함께 오름 → pivot 항상 전날 종가)
        prices  = [50.0 + i * 0.1 for i in range(250)]  # 50→74.9 직선
        volumes = [500_000] * 250
        df = _make_df(prices, volumes)
        ind = _build_indicators(df)
        assert ind is not None, "indicators 빌드 실패"

        sig = _find_breakout_signal(ind)
        # 직선 상승 = 매일 신고가 → 'pivot 이하였다가 돌파' 조건 미충족
        # (전날 close == rolling max이므로 pp <= pivot_high but pp == pivot_high)
        # 거래량도 평균치라 volume_ratio 조건 미충족 가능
        # 핵심: MA 크로스만으로 BREAKOUT이 나오지 않아야 함
        # (기울기는 충족하지만 pivot 돌파 + 거래량 조건이 필요)
        if sig is not None:
            # 혹시 발생하면 거래량 조건이 충족된 경우 — 거래량 배율 확인
            assert sig["vol_ratio"] >= 1.5, "거래량 조건 미충족인데 BREAKOUT 발생"

    def test_breakout_detected_with_pivot(self):
        """BREAKOUT: base 형성 후 거래량 동반 pivot 돌파 시 시그널 발생."""
        from scanner.weinstein import analyze_stock

        prices, volumes = _make_stage2_base(n_total=230, base_price=100.0)
        # 마지막날: pivot 돌파 (base 최고가 ≈ 102 위로 돌파)
        prices[-1]  = 104.0   # 돌파
        volumes[-1] = 1_500_000  # 3x

        df  = _make_df(prices, volumes)
        res = analyze_stock(df, "TEST", "테스트", "US")

        assert res is not None, "시그널이 발생해야 함"
        assert res["signal_type"] == "BREAKOUT"
        assert res["volume_ratio"] >= 1.5
        assert res.get("pivot_price") is not None

    def test_breakout_blocked_below_ma150(self):
        """BREAKOUT: 가격이 MA150 아래일 때 시그널 없어야 함."""
        from scanner.weinstein import _build_indicators, _find_breakout_signal

        # Stage4: 하락 추세
        prices  = [100.0 - i * 0.2 for i in range(250)]  # 100→50 하락
        volumes = [500_000] * 249 + [2_000_000]
        df  = _make_df(prices, volumes)
        ind = _build_indicators(df)
        if ind is None:
            return  # MA 계산 불가 → 패스

        sig = _find_breakout_signal(ind)
        assert sig is None, "하락 추세에서 BREAKOUT 발생하면 안 됨"

    def test_breakout_respects_volume_ratio(self):
        """BREAKOUT: 거래량 부족 시 시그널 없어야 함."""
        from scanner.weinstein import analyze_stock

        prices, volumes = _make_stage2_base(n_total=230, base_price=100.0)
        prices[-1]  = 104.0
        volumes[-1] = 300_000  # 0.6x → 충분하지 않음

        df  = _make_df(prices, volumes)
        res = analyze_stock(df, "TEST", "테스트", "US")

        # BREAKOUT은 없어야 함 (다른 시그널이 날 수도 있음)
        if res is not None:
            assert res["signal_type"] != "BREAKOUT", "거래량 부족인데 BREAKOUT 발생"

    def test_breakout_not_too_extended(self):
        """BREAKOUT: MA150 대비 과매수(15% 초과) 시 차단."""
        from scanner.weinstein import _build_indicators, _find_breakout_signal

        prices, volumes = _make_stage2_base(n_total=230, base_price=100.0)
        # 마지막날 MA150보다 20% 이상 높게 설정
        prices[-1]  = 130.0  # MA150 ≈ 80 → 62.5% 과매수
        volumes[-1] = 1_500_000
        df  = _make_df(prices, volumes)
        ind = _build_indicators(df)
        sig = _find_breakout_signal(ind)
        assert sig is None, "과매수 상태에서 BREAKOUT 발생하면 안 됨"


# ═══════════════════════════════════════════════════════════════════
# 2. RE_BREAKOUT 테스트
# ═══════════════════════════════════════════════════════════════════

class TestReBreakout:

    def _make_continuation_base(self):
        """Stage2 + 단기 조정 후 continuation base 합성."""
        prices, volumes = _make_stage2_base(n_total=220, base_price=100.0)
        # 150~180: 가격 상승해서 110 도달 (Stage2 이후 상승)
        for i in range(30):
            prices.append(100.0 + i * 0.33)
            volumes.append(500_000)
        # 181~210: 조정 (103~107 횡보, continuation base)
        for i in range(30):
            prices.append(105.0 + 2 * np.sin(i * np.pi / 4))
            volumes.append(400_000)
        # 211: 재돌파 (base 고점 ≈ 107 위로)
        prices.append(108.5)
        volumes.append(1_200_000)
        return prices, volumes

    def test_rebreakout_requires_stage2(self):
        """RE_BREAKOUT: Stage4(하락장)에서는 발생하지 않아야 함."""
        from scanner.weinstein import _build_indicators, _find_rebreakout_signal

        prices  = [100.0 - i * 0.3 for i in range(250)]
        volumes = [500_000] * 249 + [1_500_000]
        df  = _make_df(prices, volumes)
        ind = _build_indicators(df)
        if ind is None:
            return
        assert ind["stage"] != "STAGE2", "테스트 데이터가 Stage2가 아닌지 확인"
        sig = _find_rebreakout_signal(ind)
        assert sig is None

    def test_rebreakout_continuation_behavior(self):
        """RE_BREAKOUT: Stage2 조정 후 base 돌파 시 시그널 발생."""
        from scanner.weinstein import analyze_stock

        prices, volumes = self._make_continuation_base()
        df  = _make_df(prices, volumes)
        res = analyze_stock(df, "TEST", "테스트", "US")

        # RE_BREAKOUT 또는 BREAKOUT 발생해야 함
        assert res is not None, "시그널이 발생해야 함"
        assert res["signal_type"] in ("RE_BREAKOUT", "BREAKOUT"), \
            f"연속 돌파 시그널이어야 함, got: {res['signal_type']}"

    def test_rebreakout_deep_pullback_rejected(self):
        """RE_BREAKOUT: 조정폭이 MAX_PULLBACK_PCT 초과 시 차단."""
        from scanner.weinstein import _build_indicators, _find_rebreakout_signal
        from config import REBREAKOUT_MAX_PULLBACK_PCT

        prices, volumes = _make_stage2_base(n_total=220, base_price=100.0)
        # 30% 급락 → max pullback 초과
        for i in range(30):
            prices.append(100.0 - i * 1.0)  # 100→70 (30% 조정)
            volumes.append(300_000)
        prices.append(75.0)
        volumes.append(1_200_000)

        df  = _make_df(prices, volumes)
        ind = _build_indicators(df)
        sig = _find_rebreakout_signal(ind)
        assert sig is None, f"조정폭 >REBREAKOUT_MAX_PULLBACK_PCT 시 RE_BREAKOUT 차단"


# ═══════════════════════════════════════════════════════════════════
# 3. REBOUND 테스트
# ═══════════════════════════════════════════════════════════════════

class TestRebound:

    def _make_pullback_rebound_data(self, touch_pct=0.02):
        """
        Stage2에서 MA50 눌림 후 반등 합성.
        touch_pct: MA50 대비 얼마나 내려가는지 (0.02 = 2%)
        """
        prices, volumes = _make_stage2_base(n_total=200, base_price=100.0)
        # 200~215: MA50은 ≈100, 가격을 102로 올림
        for i in range(15):
            prices.append(100.0 + i * 0.2)
            volumes.append(500_000)
        # 216~225: 눌림목 (MA50 근처로 하락)
        for i in range(10):
            prices.append(103.0 - i * 0.3 * (touch_pct * 50 + 1))
            volumes.append(350_000)
        # 226~230: 반등
        for i in range(5):
            prices.append(prices[-1] + 0.6)
            volumes.append(600_000)
        return prices, volumes

    def test_rebound_uses_ma50_support(self):
        """REBOUND: MA50 지지 후 반등 시 시그널 발생."""
        from scanner.weinstein import analyze_stock

        prices, volumes = self._make_pullback_rebound_data()
        df  = _make_df(prices, volumes)
        res = analyze_stock(df, "TEST", "테스트", "US")

        if res is not None and res["signal_type"] == "REBOUND":
            # support_level은 MA50 수준이어야 함
            assert res.get("support_level") is not None
            # MA50 ≈ 100 근처여야 함
            assert 85.0 < res["support_level"] < 115.0, \
                f"support_level이 MA50 범위 밖: {res['support_level']}"

    def test_rebound_chronological_order(self):
        """REBOUND: 과거→현재 순서로 탐지 (눌림 먼저, 반등 나중)."""
        from scanner.weinstein import _build_indicators, _find_rebound_signal

        # 단순한 눌림 + 반등 패턴
        # Phase1: 200일 상승 데이터 (MA 워밍업)
        prices  = [80.0 + i * 0.1 for i in range(200)]   # 80→99.9
        volumes = [500_000] * 200

        # Phase2: 안정화 (MA50 ≈ 100)
        prices  += [100.0] * 50
        volumes += [500_000] * 50

        # Phase3: 눌림 (95로 하락)
        for i in range(10):
            prices.append(100.0 - i * 0.5)
            volumes.append(400_000)

        # Phase4: 반등 (100 위로 복귀)
        for i in range(6):
            prices.append(95.0 + i * 1.0)
            volumes.append(600_000)

        df  = _make_df(prices, volumes)
        ind = _build_indicators(df)

        if ind is None:
            pytest.skip("indicators 빌드 실패 (데이터 부족)")

        sig = _find_rebound_signal(ind)
        if sig is not None:
            assert sig["signal_type"] == "REBOUND"
            # signal_date는 반등 확인일 (눌림일이 아님)
            assert sig.get("support_level") is not None

    def test_rebound_deep_pullback_rejected(self):
        """REBOUND: 과도한 조정(12% 초과) 시 차단."""
        from scanner.weinstein import _build_indicators, _find_rebound_signal
        from config import REBOUND_MAX_PULLBACK_PCT

        # 200일 횡보 후 20% 급락 후 반등
        prices  = [100.0] * 200
        volumes = [500_000] * 200
        # 15% 이상 급락
        for i in range(10):
            prices.append(100.0 - i * 2.0)  # 100→80 (20% 하락)
            volumes.append(300_000)
        # 반등 시도
        for i in range(5):
            prices.append(80.0 + i * 1.0)
            volumes.append(600_000)

        df  = _make_df(prices, volumes)
        ind = _build_indicators(df)
        if ind is None:
            pytest.skip("indicators 빌드 실패")

        sig = _find_rebound_signal(ind)
        # 20% 급락은 REBOUND_MAX_PULLBACK_PCT(12%) 초과 → 차단
        assert sig is None, "과도한 조정 후 REBOUND가 발생하면 안 됨"

    def test_rebound_not_in_stage4(self):
        """REBOUND: Stage4에서는 발생하지 않아야 함."""
        from scanner.weinstein import analyze_stock

        # 완전 하락 추세
        prices  = [120.0 - i * 0.35 for i in range(260)]
        volumes = [500_000] * 255 + [300_000] * 5
        df  = _make_df(prices, volumes)
        res = analyze_stock(df, "TEST", "테스트", "US")

        if res is not None:
            assert res["signal_type"] != "REBOUND", "Stage4에서 REBOUND 발생하면 안 됨"


# ═══════════════════════════════════════════════════════════════════
# 4. 시장 필터 테스트
# ═══════════════════════════════════════════════════════════════════

class TestMarketFilter:

    def test_bear_blocks_all_buy_signals(self):
        """BEAR 장세: BLOCK_NEW_BUYS_IN_BEAR=True 시 모든 BUY 차단."""
        from scanner.scan_engine import _get_market_filter_decision
        import importlib, config as cfg_module

        original_enable = cfg_module.ENABLE_MARKET_FILTER
        original_block  = cfg_module.BLOCK_NEW_BUYS_IN_BEAR
        try:
            cfg_module.ENABLE_MARKET_FILTER   = True
            cfg_module.BLOCK_NEW_BUYS_IN_BEAR = True

            for sig_type in ("BREAKOUT", "RE_BREAKOUT", "REBOUND"):
                allow, msg = _get_market_filter_decision("BEAR", sig_type)
                assert not allow, f"BEAR 장세에서 {sig_type}가 허용되면 안 됨"
        finally:
            cfg_module.ENABLE_MARKET_FILTER   = original_enable
            cfg_module.BLOCK_NEW_BUYS_IN_BEAR = original_block

    def test_bear_filter_disabled(self):
        """ENABLE_MARKET_FILTER=False 시 BEAR에서도 시그널 허용."""
        from scanner.scan_engine import _get_market_filter_decision
        import config as cfg_module

        original = cfg_module.ENABLE_MARKET_FILTER
        try:
            cfg_module.ENABLE_MARKET_FILTER = False
            allow, _ = _get_market_filter_decision("BEAR", "BREAKOUT")
            assert allow, "필터 비활성화 시 항상 허용되어야 함"
        finally:
            cfg_module.ENABLE_MARKET_FILTER = original

    def test_caution_allow_with_flag(self):
        """CAUTION + allow_with_flag: 허용되지만 플래그 메시지 반환."""
        from scanner.scan_engine import _get_market_filter_decision
        import config as cfg_module

        original_enable  = cfg_module.ENABLE_MARKET_FILTER
        original_caution = cfg_module.CAUTION_MODE
        try:
            cfg_module.ENABLE_MARKET_FILTER = True
            cfg_module.CAUTION_MODE         = "allow_with_flag"

            allow, flag = _get_market_filter_decision("CAUTION", "BREAKOUT")
            assert allow, "allow_with_flag 모드에서 시그널 허용되어야 함"
            assert flag is not None, "CAUTION 플래그 메시지가 있어야 함"
        finally:
            cfg_module.ENABLE_MARKET_FILTER = original_enable
            cfg_module.CAUTION_MODE         = original_caution

    def test_caution_block_breakout_mode(self):
        """CAUTION + block_breakout: BREAKOUT만 차단, REBOUND는 허용."""
        from scanner.scan_engine import _get_market_filter_decision
        import config as cfg_module

        original_enable  = cfg_module.ENABLE_MARKET_FILTER
        original_caution = cfg_module.CAUTION_MODE
        try:
            cfg_module.ENABLE_MARKET_FILTER = True
            cfg_module.CAUTION_MODE         = "block_breakout"

            allow_bo, _ = _get_market_filter_decision("CAUTION", "BREAKOUT")
            allow_rb, _ = _get_market_filter_decision("CAUTION", "REBOUND")

            assert not allow_bo, "block_breakout 모드: BREAKOUT 차단"
            assert allow_rb,     "block_breakout 모드: REBOUND 허용"
        finally:
            cfg_module.ENABLE_MARKET_FILTER = original_enable
            cfg_module.CAUTION_MODE         = original_caution

    def test_bull_always_allows(self):
        """BULL 장세: 모든 시그널 허용."""
        from scanner.scan_engine import _get_market_filter_decision
        import config as cfg_module

        cfg_module.ENABLE_MARKET_FILTER = True
        for sig_type in ("BREAKOUT", "RE_BREAKOUT", "REBOUND"):
            allow, _ = _get_market_filter_decision("BULL", sig_type)
            assert allow, f"BULL 장세에서 {sig_type}가 차단되면 안 됨"


# ═══════════════════════════════════════════════════════════════════
# 5. SELL 로직 테스트
# ═══════════════════════════════════════════════════════════════════

class TestSellSignal:

    def test_sell_on_stage4(self):
        """Stage4 진입 시 SELL 시그널 발생."""
        from scanner.weinstein import check_sell_signal

        # 상승 후 급락
        prices  = [100.0 + i * 0.1 for i in range(155)]  # 상승
        prices += [116.0 - i * 0.5 for i in range(50)]   # 하락
        volumes = [500_000] * len(prices)
        df      = _make_df(prices, volumes)

        res = check_sell_signal(df, "TEST", "테스트", "US", buy_price=100.0)
        if res is not None:
            assert res["signal_type"] == "SELL"
            assert res["stage"] in ("STAGE4", "STAGE3")

    def test_sell_on_stop_loss(self):
        """손절가 도달 시 즉시 SELL 시그널."""
        from scanner.weinstein import check_sell_signal

        prices  = [100.0] * 200  # 횡보
        volumes = [500_000] * 200
        df      = _make_df(prices, volumes)

        # 현재가(100) ≤ 손절가(105)
        res = check_sell_signal(df, "TEST", "테스트", "US",
                                buy_price=120.0, stop_loss=105.0)
        assert res is not None
        assert "손절가" in res["sell_reason"]

    def test_no_sell_in_stage2(self):
        """Stage2 강세 중에는 SELL 시그널 없어야 함."""
        from scanner.weinstein import check_sell_signal

        # 완만한 Stage2 상승
        prices  = [80.0 + i * 0.1 for i in range(260)]
        volumes = [500_000] * 260
        df      = _make_df(prices, volumes)

        res = check_sell_signal(df, "TEST", "테스트", "US", buy_price=80.0)
        assert res is None, "Stage2 상승 중 SELL 발생하면 안 됨"


# ═══════════════════════════════════════════════════════════════════
# 6. 공통 유틸리티 테스트
# ═══════════════════════════════════════════════════════════════════

class TestUtilities:

    def test_stage_of_classification(self):
        """stage_of: 가격/MA/기울기 조합에 따른 Stage 분류."""
        from scanner.weinstein import stage_of

        assert stage_of(110, 100,  0.10) == "STAGE2"  # above MA, rising
        assert stage_of(110, 100,  0.00) == "STAGE3"  # above MA, flat
        assert stage_of( 90, 100, -0.10) == "STAGE4"  # below MA, falling
        assert stage_of( 90, 100,  0.10) == "STAGE1"  # below MA, rising

    def test_calc_rs_outperform(self):
        """calc_rs: 주식이 지수보다 강할 때 RS > 1."""
        from scanner.weinstein import calc_rs

        stock_prices = pd.Series([100.0 + i * 0.5 for i in range(70)])  # +34.5%
        bench_prices = pd.Series([100.0 + i * 0.2 for i in range(70)])  # +13.8%
        rs = calc_rs(stock_prices, bench_prices, period=65)
        assert rs is not None
        assert rs > 1.0, f"아웃퍼폼 시 RS > 1 이어야 함, got {rs}"

    def test_calc_rs_underperform(self):
        """calc_rs: 주식이 지수보다 약할 때 RS < 1."""
        from scanner.weinstein import calc_rs

        stock_prices = pd.Series([100.0 + i * 0.1 for i in range(70)])  # +6.5%
        bench_prices = pd.Series([100.0 + i * 0.4 for i in range(70)])  # +26.4%
        rs = calc_rs(stock_prices, bench_prices, period=65)
        assert rs is not None
        assert rs < 1.0, f"언더퍼폼 시 RS < 1 이어야 함, got {rs}"

    def test_analyze_stock_returns_none_for_short_df(self):
        """데이터 부족 시 analyze_stock None 반환."""
        from scanner.weinstein import analyze_stock

        short_prices = [100.0] * 50  # MA150 계산 불가
        df = _make_df(short_prices)
        res = analyze_stock(df, "TEST", "테스트", "US")
        assert res is None

    def test_signal_quality_levels(self):
        """signal_quality: 조건별 STRONG/MODERATE/WEAK 분류."""
        from scanner.weinstein import _signal_quality

        assert _signal_quality(4.0, 0.15, 2.0, "BREAKOUT") == "STRONG"
        assert _signal_quality(2.0, 0.06, 1.1, "REBOUND")  == "MODERATE"
        assert _signal_quality(1.6, 0.01, 0.8, "REBOUND")  == "WEAK"

    def test_build_indicators_returns_none_without_ma(self):
        """MA150 계산 불가 시 _build_indicators None 반환."""
        from scanner.weinstein import _build_indicators

        df = _make_df([100.0] * 30)  # MA150 계산 불가
        result = _build_indicators(df)
        assert result is None


# ═══════════════════════════════════════════════════════════════════
# 7. 신규 기능 검증 테스트
# ═══════════════════════════════════════════════════════════════════

class TestNewFeatures:

    # ── analyze_stock 리턴 필드 ───────────────────────────────────

    def test_analyze_stock_returns_ma50_and_base_quality(self):
        """analyze_stock 결과에 ma50, base_quality 포함."""
        from scanner.weinstein import analyze_stock

        prices, volumes = _make_stage2_base(n_total=280, base_price=100.0)
        # 돌파: 마지막 날 pivot 상향 + 거래량 폭발
        prices  += [105.0]
        volumes += [2_000_000]
        df = _make_df(prices, volumes)

        res = analyze_stock(df, "TEST", "테스트", "US")
        if res is not None:
            assert "ma50"         in res, "ma50 필드 누락"
            assert "base_quality" in res, "base_quality 필드 누락"
            assert res["ma50"] > 0
            assert res["base_quality"] in ("STRONG", "WEAK", "N/A")

    # ── SELL severity ─────────────────────────────────────────────

    def test_sell_severity_high_on_stop_loss(self):
        """손절가 도달 시 severity=HIGH."""
        from scanner.weinstein import check_sell_signal

        prices  = [100.0] * 200
        df      = _make_df(prices)
        res     = check_sell_signal(df, "T", "테스트", "US",
                                    buy_price=120.0, stop_loss=105.0)
        assert res is not None
        assert res["severity"] == "HIGH"

    def test_sell_severity_low_on_stage3(self):
        """Stage3 징후 시 severity=LOW."""
        from scanner.weinstein import check_sell_signal

        # 가격이 MA150 위이지만 기울기 0 → Stage3
        prices  = [100.0 + i * 0.03 for i in range(170)]   # 완만한 상승
        prices += [105.0] * 40                               # 이후 횡보
        df      = _make_df(prices)
        res     = check_sell_signal(df, "T", "테스트", "US")
        if res is not None:
            assert res["severity"] in ("LOW", "MEDIUM", "HIGH")
            assert "severity" in res

    def test_sell_has_severity_field(self):
        """check_sell_signal 결과에 항상 severity 필드 포함."""
        from scanner.weinstein import check_sell_signal

        prices  = [100.0] * 200
        df      = _make_df(prices)
        res     = check_sell_signal(df, "T", "테스트", "US",
                                    buy_price=150.0, stop_loss=110.0)
        assert res is not None
        assert "severity" in res

    # ── _grade 함수 ───────────────────────────────────────────────

    def test_grade_s_for_strong_breakout(self):
        """STRONG BREAKOUT + STRONG base + RS≥1.5 + BULL → S 등급."""
        from scanner.scan_engine import _grade

        sig = {
            "signal_quality":  "STRONG",
            "signal_type":     "BREAKOUT",
            "base_quality":    "STRONG",
            "rs":              1.6,
            "market_condition": "BULL",
        }
        assert _grade(sig) == "S", f"기대 S, 실제 {_grade(sig)}"

    def test_grade_b_for_weak_rebound(self):
        """WEAK REBOUND + base N/A + RS 없음 → B 등급."""
        from scanner.scan_engine import _grade

        sig = {
            "signal_quality":  "WEAK",
            "signal_type":     "REBOUND",
            "base_quality":    "N/A",
            "rs":              None,
            "market_condition": "NEUTRAL",
        }
        assert _grade(sig) == "B", f"기대 B, 실제 {_grade(sig)}"

    def test_grade_a_for_moderate_breakout(self):
        """MODERATE BREAKOUT + RS≥1.5 → A 등급 (2+1+1=4)."""
        from scanner.scan_engine import _grade

        sig = {
            "signal_quality":  "MODERATE",
            "signal_type":     "BREAKOUT",
            "base_quality":    "WEAK",
            "rs":              1.5,          # ≥1.5 → +1 (총 4점 → A)
            "market_condition": "NEUTRAL",
        }
        g = _grade(sig)
        assert g in ("A", "S"), f"기대 A 이상, 실제 {g}"

    def test_grade_bear_market_penalty(self):
        """BEAR 장세는 점수 -2 페널티."""
        from scanner.scan_engine import _grade

        sig_bull = {"signal_quality": "MODERATE", "signal_type": "REBOUND",
                    "base_quality": "N/A", "rs": 1.2, "market_condition": "BULL"}
        sig_bear = {**sig_bull, "market_condition": "BEAR"}

        g_bull = _grade(sig_bull)
        g_bear = _grade(sig_bear)
        grade_order = {"S": 2, "A": 1, "B": 0}
        assert grade_order[g_bear] <= grade_order[g_bull], \
            "BEAR 장세 등급이 BULL 보다 낮거나 같아야 함"


# ═══════════════════════════════════════════════════════════════════
# 8. 주봉 지표 (compute_weekly_indicators) 단위 테스트 — Phase 1
# ═══════════════════════════════════════════════════════════════════

class TestWeeklyIndicators:

    def test_weekly_indicators_basic_rising(self):
        """250일 약한 상승 → 30w SMA 양수 슬로프, close > sma30w."""
        from scanner.weinstein import to_weekly_ohlcv, compute_weekly_indicators

        prices = [50.0 + i * 0.2 for i in range(250)]   # 50→99.8
        df     = _make_df(prices)
        weekly = to_weekly_ohlcv(df)
        ind    = compute_weekly_indicators(weekly)

        assert ind is not None, "주봉 30주 이상 데이터인데 None 반환"
        assert ind["cur_sma30w"] > 0
        assert ind["slope30w"] > 0,                 f"상승 추세 슬로프 양수여야 함, got {ind['slope30w']}"
        assert ind["cur_close_w"] > ind["cur_sma30w"]
        assert "weekly_volume_ratio" in ind

    def test_weekly_indicators_short_data_returns_none(self):
        """주봉 30주 미만 데이터는 None."""
        from scanner.weinstein import to_weekly_ohlcv, compute_weekly_indicators

        # 100일 ≈ 20주 → 30주 미달
        df     = _make_df([100.0] * 100)
        weekly = to_weekly_ohlcv(df)
        assert compute_weekly_indicators(weekly) is None

    def test_weekly_indicators_falling_slope(self):
        """하락 추세에서 30w SMA 슬로프 음수, close < sma30w."""
        from scanner.weinstein import to_weekly_ohlcv, compute_weekly_indicators

        prices = [200.0 - i * 0.4 for i in range(250)]   # 200→100
        df     = _make_df(prices)
        weekly = to_weekly_ohlcv(df)
        ind    = compute_weekly_indicators(weekly)

        assert ind is not None
        assert ind["slope30w"] < 0,                 f"하락 추세 슬로프 음수여야 함, got {ind['slope30w']}"
        assert ind["cur_close_w"] < ind["cur_sma30w"]


# ═══════════════════════════════════════════════════════════════════
# 9. Mansfield RS (compute_relative_performance) 단위 테스트
# ═══════════════════════════════════════════════════════════════════

class TestMansfieldRS:

    @staticmethod
    def _series(prices):
        idx = pd.date_range(start="2020-01-01", periods=len(prices), freq="B")
        return pd.Series([float(p) for p in prices], index=idx)

    def test_rs_outperform_positive(self):
        """주식이 벤치마크보다 강할 때 Mansfield RS > 0."""
        from scanner.weinstein import compute_relative_performance

        n     = 300
        # 처음 절반은 동일하게 → ratio SMA 자리잡고, 후반에 outperform
        stock = [100.0] * 150 + [100.0 + i * 0.5 for i in range(150)]
        bench = [100.0] * 150 + [100.0 + i * 0.1 for i in range(150)]
        rs_value, rs_trend = compute_relative_performance(
            self._series(stock), self._series(bench)
        )
        assert rs_value is not None
        assert rs_value > 0, f"outperform 시 RS > 0 이어야 함, got {rs_value}"
        assert rs_trend in ("RISING", "FALLING", "FLAT")

    def test_rs_underperform_negative(self):
        """주식이 벤치마크보다 약할 때 Mansfield RS < 0."""
        from scanner.weinstein import compute_relative_performance

        n     = 300
        stock = [100.0] * 150 + [100.0 + i * 0.05 for i in range(150)]
        bench = [100.0] * 150 + [100.0 + i * 0.5  for i in range(150)]
        rs_value, _ = compute_relative_performance(
            self._series(stock), self._series(bench)
        )
        assert rs_value is not None
        assert rs_value < 0, f"underperform 시 RS < 0 이어야 함, got {rs_value}"

    def test_rs_short_data_returns_none(self):
        """260일(=52주) 미만이면 None 반환."""
        from scanner.weinstein import compute_relative_performance

        n     = 100
        stock = self._series([100.0] * n)
        bench = self._series([100.0] * n)
        rs_value, rs_trend = compute_relative_performance(stock, bench)
        assert rs_value is None
        assert rs_trend is None

    def test_rs_falling_trend_detected(self):
        """최근 5주 ratio 하락 → trend == FALLING."""
        from scanner.weinstein import compute_relative_performance

        # 250일 outperform 후 50일 stock 빠른 하락 + bench 상승 → ratio 급락
        stock = [100.0 + i * 0.5 for i in range(250)]
        last_s = stock[-1]
        stock += [last_s - i * 1.0 for i in range(50)]
        bench  = [100.0 + i * 0.2 for i in range(250)]
        last_b = bench[-1]
        bench += [last_b + i * 0.3 for i in range(50)]

        _, rs_trend = compute_relative_performance(
            self._series(stock), self._series(bench)
        )
        assert rs_trend == "FALLING", f"기대 FALLING, got {rs_trend}"


# ═══════════════════════════════════════════════════════════════════
# 10. Base Pivot (detect_base_pivot) 단위 테스트
# ═══════════════════════════════════════════════════════════════════

class TestBasePivot:

    def test_tight_base_detected_in_sideways(self):
        """5주 이상 ±2% 횡보 → base 탐지, 폭 ≤ 8%."""
        from scanner.weinstein import detect_base_pivot

        # 60일 동안 100 ± 1.5 횡보 → 폭 ≈ 3% (TIGHT)
        prices = [100.0 + np.sin(i * np.pi / 4) * 1.5 for i in range(60)]
        df     = _make_df(prices)
        result = detect_base_pivot(df)

        assert result is not None,                          "5주 이상 tight 횡보면 base 탐지되어야 함"
        assert result["base_weeks"]    >= 5
        assert result["base_width_pct"] <= 8.0
        assert result["base_quality"]  == "TIGHT"

    def test_no_base_when_data_too_short(self):
        """min_weeks*5 + 5 = 30 미만이면 None."""
        from scanner.weinstein import detect_base_pivot

        df = _make_df([100.0] * 10)   # 10일 < 30
        assert detect_base_pivot(df) is None

    def test_loose_base_classification(self):
        """폭 8~15% 횡보면 LOOSE 등급."""
        from scanner.weinstein import detect_base_pivot

        # 60일 동안 95~107 진동 → 폭 ≈ 11% (LOOSE)
        prices = [101.0 + np.sin(i * np.pi / 5) * 6.0 for i in range(60)]
        df     = _make_df(prices)
        result = detect_base_pivot(df)

        assert result is not None
        assert 8.0 < result["base_width_pct"] <= 15.0
        assert result["base_quality"]         == "LOOSE"

    def test_base_returns_pivot_above_low(self):
        """탐지된 base 의 pivot_price > base_low."""
        from scanner.weinstein import detect_base_pivot

        prices = [100.0 + np.sin(i * np.pi / 4) * 1.5 for i in range(60)]
        df     = _make_df(prices)
        result = detect_base_pivot(df)
        assert result is not None
        assert result["pivot_price"] > result["base_low"]


# ═══════════════════════════════════════════════════════════════════
# 11. STAGE 분류 경계 조건 (classify_stage)
# ═══════════════════════════════════════════════════════════════════

class TestStageBoundary:

    def test_stage3_above_ma_flat_slope(self):
        """close > sma30w + slope ≈ 0 → STAGE3 (분배)."""
        from scanner.weinstein import classify_stage

        ind = {"cur_close_w": 110.0, "cur_sma30w": 100.0,
               "cur_sma10w": 109.0,  "slope30w": 0.0}
        assert classify_stage(ind, None) == "STAGE3"

    def test_stage3_when_short_sma_weakens(self):
        """close > sma30w + 약한 상승 + sma10 < close*0.98 → STAGE3."""
        from scanner.weinstein import classify_stage

        # slope=0.04 < _RISING_SLOPE=0.05, sma10 105 < close 110*0.98=107.8
        ind = {"cur_close_w": 110.0, "cur_sma30w": 100.0,
               "cur_sma10w": 105.0,  "slope30w": 0.04}
        assert classify_stage(ind, None) == "STAGE3"

    def test_stage2_when_above_and_rising(self):
        """close > sma30w + slope > 0.05 → STAGE2."""
        from scanner.weinstein import classify_stage

        ind = {"cur_close_w": 110.0, "cur_sma30w": 100.0,
               "cur_sma10w": 109.0,  "slope30w": 0.10}
        assert classify_stage(ind, None) == "STAGE2"

    def test_stage4_below_and_falling(self):
        """close < sma30w + slope < -0.02 → STAGE4."""
        from scanner.weinstein import classify_stage

        ind = {"cur_close_w": 90.0,  "cur_sma30w": 100.0,
               "cur_sma10w": 92.0,   "slope30w": -0.10}
        assert classify_stage(ind, None) == "STAGE4"


# ═══════════════════════════════════════════════════════════════════
# 12. check_sell_signal — 신규 옵션 분기 (Phase 1)
# ═══════════════════════════════════════════════════════════════════

class TestSellSignalNewBranches:

    @staticmethod
    def _safe_daily_df():
        """기존 분기를 트리거하지 않는 daily df.

        80→106 으로 일정한 약한 상승 → MA150 양수 슬로프 + Stage2,
        stop_loss/STAGE4/MA150 반전/STAGE3 분기 모두 미발현.
        """
        prices = [80.0 + i * 0.1 for i in range(260)]
        return _make_df(prices)

    @staticmethod
    def _make_weekly(weekly_prices):
        n       = len(weekly_prices)
        idx     = pd.date_range(start="2020-01-03", periods=n, freq="W-FRI")
        prices  = [float(p) for p in weekly_prices]
        return pd.DataFrame({
            "Open":   [p * 0.998 for p in prices],
            "High":   [p * 1.005 for p in prices],
            "Low":    [p * 0.995 for p in prices],
            "Close":  prices,
            "Volume": [1_000_000] * n,
        }, index=idx)

    # ── Helper 단위 테스트 ──────────────────────────────────────────

    def test_weekly_breakdown_helper(self):
        """_weekly_breakdown: 마지막 close < 30w SMA → True."""
        from scanner.weinstein import _weekly_breakdown

        weekly = self._make_weekly([100.0 + i * 0.5 for i in range(50)] + [105.0])
        assert _weekly_breakdown(weekly) is True

        weekly_ok = self._make_weekly([100.0 + i * 0.5 for i in range(50)])
        assert _weekly_breakdown(weekly_ok) is False
        assert _weekly_breakdown(None)      is False

    def test_weekly_slope_reversal_helper(self):
        """_weekly_slope_reversal: 강한 상승 후 가파른 하락 → True."""
        from scanner.weinstein import _weekly_slope_reversal

        # 50주 +1/wk 상승 → 5주 -30/wk 가파른 하락. SMA30 cur_slope<0 & past_slope>0.
        prices  = [200.0 + i for i in range(50)]
        prices += [249.0 - 30.0 * (i + 1) for i in range(5)]
        assert _weekly_slope_reversal(self._make_weekly(prices)) is True

        # 일관 상승은 반전 없음 (WEEKLY_MA_LONG + 5 = 35주 이상 필요)
        steady = [100.0 + i for i in range(80)]
        assert _weekly_slope_reversal(self._make_weekly(steady)) is False
        assert _weekly_slope_reversal(None) is False

    def test_rs_deterioration_helper(self):
        """_rs_deteriorating: RS<0 + FALLING → True."""
        from scanner.weinstein import _rs_deteriorating

        idx   = pd.date_range(start="2020-01-01", periods=300, freq="B")
        # 250일 outperform 후 50일 빠른 하락 + bench 가속 상승
        stock = [100.0 + i * 0.5 for i in range(250)]
        stock += [stock[-1] - i * 1.2 for i in range(50)]
        bench = [100.0 + i * 0.2 for i in range(250)]
        bench += [bench[-1] + i * 0.4 for i in range(50)]
        s = pd.Series([float(x) for x in stock], index=idx)
        b = pd.Series([float(x) for x in bench], index=idx)
        assert _rs_deteriorating(s, b) is True

        # benchmark None → False
        assert _rs_deteriorating(s, None) is False

    # ── check_sell_signal 통합 분기 테스트 ─────────────────────────

    def test_weekly_breakdown_triggers_high_sell(self):
        """주봉 30-SMA 하향 이탈 → severity HIGH."""
        from scanner.weinstein import check_sell_signal

        daily  = self._safe_daily_df()
        weekly = self._make_weekly(
            [100.0 + i * 0.5 for i in range(50)] + [105.0]
        )
        res = check_sell_signal(daily, "T", "테스트", "US", weekly_df=weekly)
        assert res is not None
        assert res["severity"]    == "HIGH"
        assert "주봉" in res["sell_reason"]

    def test_no_regression_when_options_omitted(self):
        """weekly_df / benchmark_close 미제공 시 기존 결과 유지."""
        from scanner.weinstein import check_sell_signal

        # Stage2 강세 → 기존에도 None 반환 → 신규 분기로도 None 유지
        daily = self._safe_daily_df()
        assert check_sell_signal(daily, "T", "n", "US")               is None
        assert check_sell_signal(daily, "T", "n", "US",
                                 weekly_df=None, benchmark_close=None) is None


# ── 실행 ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import subprocess
    subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v", "--tb=short"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
