"""
Phase 2 — scanner orchestration 통합/회귀 테스트.

monkeypatch로 외부 의존(DB, KR/US 페치, telegram, market_analysis)을 차단하고,
- _check_watchlist 가 weekly_df / benchmark_close 옵션을 채워 check_sell_signal
  을 호출하는지,
- kr_stocks.fetch_ohlcv / us_stocks.fetch_ohlcv 어댑터가 올바른 underlying
  fetcher 로 라우팅되는지,
- 외부 페치 실패가 graceful 하게 None 으로 전파되는지
를 결정적으로 검증한다.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, timedelta
import pandas as pd
import pytest


def _make_daily(n=260, start_price=100.0, step=0.5):
    """Phase 2 단위 테스트용 합성 일봉 OHLCV."""
    dates  = [date(2022, 1, 1) + timedelta(days=i) for i in range(n)]
    prices = [start_price + i * step for i in range(n)]
    return pd.DataFrame({
        "Open":   [p * 0.998 for p in prices],
        "High":   [p * 1.005 for p in prices],
        "Low":    [p * 0.995 for p in prices],
        "Close":  prices,
        "Volume": [500_000.0] * n,
    }, index=pd.DatetimeIndex(dates))


# ══════════════════════════════════════════════════════════════════
# fetch_ohlcv 어댑터 라우팅
# ══════════════════════════════════════════════════════════════════

class TestFetchOhlcvKR:
    def test_kr_fetch_routes_to_get_kr_ohlcv(self, monkeypatch):
        from scanner import kr_stocks

        captured = {}

        def fake_get_kr_ohlcv(ticker, period_years=2):
            captured["ticker"] = ticker
            captured["period_years"] = period_years
            return _make_daily(n=120)

        monkeypatch.setattr(kr_stocks, "get_kr_ohlcv", fake_get_kr_ohlcv)

        result = kr_stocks.fetch_ohlcv("005930", lookback_days=730)

        assert captured["ticker"] == "005930"
        assert captured["period_years"] == 2
        assert result is not None and len(result) == 120

    def test_kr_fetch_raises_datafetcherror_on_external_failure(self, monkeypatch):
        from scanner import kr_stocks
        from scanner.errors import DataFetchError

        def boom(ticker, period_years=2):
            raise RuntimeError("FDR 다운")

        monkeypatch.setattr(kr_stocks, "get_kr_ohlcv", boom)

        # Phase 4: 외부 장애는 명시적으로 raise — 호출자가 None 과 구분 가능
        with pytest.raises(DataFetchError, match="KR fetch failed for 005930"):
            kr_stocks.fetch_ohlcv("005930", lookback_days=365)

    def test_kr_fetch_zero_lookback_returns_none(self):
        from scanner import kr_stocks
        assert kr_stocks.fetch_ohlcv("005930", lookback_days=0) is None
        assert kr_stocks.fetch_ohlcv("005930", lookback_days=-10) is None

    def test_kr_fetch_rounds_up_to_year(self, monkeypatch):
        from scanner import kr_stocks
        seen = {}

        def fake(ticker, period_years=2):
            seen["years"] = period_years
            return _make_daily(n=70)

        monkeypatch.setattr(kr_stocks, "get_kr_ohlcv", fake)
        kr_stocks.fetch_ohlcv("005930", lookback_days=400)  # 1.1년 → 2년
        assert seen["years"] == 2

        kr_stocks.fetch_ohlcv("005930", lookback_days=200)  # 0.5년 → 1년
        assert seen["years"] == 1


class TestFetchOhlcvUS:
    def test_us_fetch_routes_to_get_us_ohlcv(self, monkeypatch):
        from scanner import us_stocks

        captured = {}

        def fake_get_us_ohlcv(ticker, period="2y"):
            captured["ticker"] = ticker
            captured["period"] = period
            return _make_daily(n=120)

        monkeypatch.setattr(us_stocks, "get_us_ohlcv", fake_get_us_ohlcv)

        result = us_stocks.fetch_ohlcv("AAPL", lookback_days=730)

        assert captured["ticker"] == "AAPL"
        assert captured["period"] == "2y"
        assert result is not None and len(result) == 120

    def test_us_fetch_raises_datafetcherror_on_external_failure(self, monkeypatch):
        from scanner import us_stocks
        from scanner.errors import DataFetchError

        def boom(ticker, period="2y"):
            raise RuntimeError("yfinance 다운")

        monkeypatch.setattr(us_stocks, "get_us_ohlcv", boom)

        with pytest.raises(DataFetchError, match="US fetch failed for AAPL"):
            us_stocks.fetch_ohlcv("AAPL", lookback_days=365)

    def test_us_fetch_period_string_rounds_up(self, monkeypatch):
        from scanner import us_stocks
        seen = {}

        def fake(ticker, period="2y"):
            seen["period"] = period
            return _make_daily(n=70)

        monkeypatch.setattr(us_stocks, "get_us_ohlcv", fake)
        us_stocks.fetch_ohlcv("AAPL", lookback_days=365)
        assert seen["period"] == "1y"

        us_stocks.fetch_ohlcv("AAPL", lookback_days=1825)
        assert seen["period"] == "5y"


# ══════════════════════════════════════════════════════════════════
# _check_watchlist — weekly_df / benchmark_close 결선 확인
# ══════════════════════════════════════════════════════════════════

class _WatchItem:
    """WatchList 행 흉내 (DB 안 쓰고 attr 만)."""
    def __init__(self, ticker, name, market, buy_price=None, stop_loss=None):
        self.ticker = ticker
        self.name = name
        self.market = market
        self.buy_price = buy_price
        self.stop_loss = stop_loss
        self.is_active = True


class _FakeQuery:
    def __init__(self, items): self._items = items
    def filter(self, *a, **kw): return self
    def all(self): return self._items


class _FakeDB:
    def __init__(self, items): self._items = items
    def query(self, *a, **kw): return _FakeQuery(self._items)


class TestCheckWatchlistKwargs:
    def test_passes_weekly_and_benchmark_to_check_sell(self, monkeypatch):
        from scanner import scan_engine
        from scanner import kr_stocks, us_stocks
        import scanner.weinstein as weinstein

        kr_daily = _make_daily(n=260, start_price=200.0, step=0.5)
        us_daily = _make_daily(n=260, start_price=100.0, step=0.3)
        kr_bench = pd.Series(
            [50.0 + i * 0.1 for i in range(260)],
            index=kr_daily.index,
        )
        us_bench = pd.Series(
            [400.0 + i * 0.05 for i in range(260)],
            index=us_daily.index,
        )

        monkeypatch.setattr(kr_stocks, "get_kr_ohlcv", lambda t, *a, **kw: kr_daily)
        monkeypatch.setattr(us_stocks, "get_us_ohlcv", lambda t, *a, **kw: us_daily)

        captured = []

        def fake_check_sell_signal(df, ticker, name, market,
                                   buy_price=None, stop_loss=None,
                                   weekly_df=None, benchmark_close=None):
            captured.append({
                "ticker": ticker,
                "market": market,
                "weekly_df_is_df": isinstance(weekly_df, pd.DataFrame) and len(weekly_df) > 0,
                "benchmark_is_series": isinstance(benchmark_close, pd.Series),
            })
            return None  # 시그널 없음 → 결과 없음

        monkeypatch.setattr(weinstein, "check_sell_signal", fake_check_sell_signal)

        items = [
            _WatchItem("005930", "삼성전자", "KR"),
            _WatchItem("AAPL",   "Apple",   "US"),
        ]
        db = _FakeDB(items)
        sells = scan_engine._check_watchlist(db, kr_bench=kr_bench, us_bench=us_bench)

        assert sells == []
        assert len(captured) == 2
        kr_call = next(c for c in captured if c["market"] == "KR")
        us_call = next(c for c in captured if c["market"] == "US")
        assert kr_call["weekly_df_is_df"] is True
        assert kr_call["benchmark_is_series"] is True
        assert us_call["weekly_df_is_df"] is True
        assert us_call["benchmark_is_series"] is True

    def test_skips_when_daily_df_is_none(self, monkeypatch):
        from scanner import scan_engine
        from scanner import kr_stocks, us_stocks
        import scanner.weinstein as weinstein

        monkeypatch.setattr(kr_stocks, "get_kr_ohlcv", lambda t, *a, **kw: None)
        monkeypatch.setattr(us_stocks, "get_us_ohlcv", lambda t, *a, **kw: None)

        called = []
        monkeypatch.setattr(weinstein, "check_sell_signal",
                            lambda *a, **kw: called.append(kw) or None)

        db = _FakeDB([_WatchItem("X", "X", "KR")])
        sells = scan_engine._check_watchlist(db, kr_bench=None, us_bench=None)

        assert sells == []
        assert called == []  # 일봉 None → check_sell_signal 호출 자체 스킵

    def test_passes_none_weekly_when_resample_empty(self, monkeypatch):
        """일봉이 너무 짧아 to_weekly_ohlcv가 빈 DF를 돌려주는 경로."""
        from scanner import scan_engine
        from scanner import kr_stocks
        import scanner.weinstein as weinstein

        # 4 rows < 5 → to_weekly_ohlcv 가 빈 DataFrame 반환
        idx = pd.DatetimeIndex([date(2024, 1, 1) + timedelta(days=i) for i in range(4)])
        short = pd.DataFrame({
            "Open": [1.0]*4, "High": [1.0]*4, "Low": [1.0]*4,
            "Close": [1.0]*4, "Volume": [1.0]*4,
        }, index=idx)
        monkeypatch.setattr(kr_stocks, "get_kr_ohlcv", lambda t, *a, **kw: short)

        captured = {}
        def fake_check(df, ticker, name, market, buy_price=None, stop_loss=None,
                       weekly_df=None, benchmark_close=None):
            captured["weekly_df"] = weekly_df
            captured["bench"] = benchmark_close
            return None
        monkeypatch.setattr(weinstein, "check_sell_signal", fake_check)

        db = _FakeDB([_WatchItem("005930", "삼성전자", "KR")])
        scan_engine._check_watchlist(db, kr_bench=None, us_bench=None)

        assert captured["weekly_df"] is None  # 빈 DF는 None 으로 폴백
        assert captured["bench"] is None

    def test_external_exception_is_caught(self, monkeypatch):
        from scanner import scan_engine
        from scanner import kr_stocks

        def boom(ticker, *a, **kw):
            raise RuntimeError("network gone")

        monkeypatch.setattr(kr_stocks, "get_kr_ohlcv", boom)

        db = _FakeDB([_WatchItem("005930", "삼성전자", "KR")])
        # 예외를 raise 하면 안 된다 — 내부에서 try/except로 잡고 빈 리스트 반환.
        sells = scan_engine._check_watchlist(db, kr_bench=None, us_bench=None)
        assert sells == []


# ══════════════════════════════════════════════════════════════════
# 시장 필터 회귀 (BEAR + BREAKOUT 차단 정책 유지)
# ══════════════════════════════════════════════════════════════════

class TestMarketFilterRegression:
    def test_bear_blocks_breakout(self, monkeypatch):
        # 디폴트 config가 BLOCK_NEW_BUYS_IN_BEAR=True 라는 가정 검증.
        from scanner.scan_engine import _get_market_filter_decision
        allow, msg = _get_market_filter_decision("BEAR", "BREAKOUT")
        assert allow is False
        assert msg is not None and "BEAR" in msg

    def test_bull_allows_breakout(self):
        from scanner.scan_engine import _get_market_filter_decision
        allow, msg = _get_market_filter_decision("BULL", "BREAKOUT")
        assert allow is True

    def test_none_market_allows_anything(self):
        from scanner.scan_engine import _get_market_filter_decision
        allow, msg = _get_market_filter_decision(None, "BREAKOUT")
        assert allow is True


# ══════════════════════════════════════════════════════════════════
# _save() — Mansfield rs_value 영속화 (Phase 2)
# ══════════════════════════════════════════════════════════════════

class TestSavePersistsMansfieldRS:
    """`_save()` 가 legacy `rs` 가 아닌 Mansfield `rs_value` 를 DB 컬럼에 기록한다."""

    def _fresh_db(self):
        """SQLite in-memory + ScanResult 테이블만 사용하는 임시 세션."""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from database.models import Base

        eng = create_engine("sqlite:///:memory:",
                            connect_args={"check_same_thread": False})
        Base.metadata.create_all(eng)
        Session = sessionmaker(bind=eng)
        return Session()

    def _signal(self, **overrides):
        sig = {
            "ticker":        "TEST",
            "name":          "테스트",
            "market":        "US",
            "signal_type":   "BREAKOUT",
            "stage":         "STAGE2",
            "price":         105.0,
            "ma150":         100.0,
            "volume":        4_000_000,
            "volume_avg":    1_000_000,
            "volume_ratio":  4.0,
            "signal_date":   "2024-06-03",
            "pivot_price":   104.0,
            "support_level": 99.0,
            "market_condition": "BULL",
            "signal_quality":   "STRONG",
            "base_quality":     "STRONG",
            # legacy ratio RS 와 Mansfield RS 를 동시에 채워 영속화 경로를 검증
            "rs":          1.2,   # 절대 DB 에 들어가서는 안 됨
            "rs_value":    6.0,   # 이 값이 ScanResult.rs_value 로 기록되어야 함
            "rs_trend":    "RISING",
        }
        sig.update(overrides)
        return sig

    def test_save_writes_mansfield_rs_value_on_insert(self):
        from scanner.scan_engine import _save
        from database.models import ScanResult

        db = self._fresh_db()
        try:
            _save(db, self._signal())
            row = db.query(ScanResult).filter(
                ScanResult.ticker == "TEST",
                ScanResult.signal_date == "2024-06-03",
            ).one()
            assert row.rs_value == 6.0
        finally:
            db.close()

    def test_save_updates_mansfield_rs_value_on_existing(self):
        from scanner.scan_engine import _save
        from database.models import ScanResult

        db = self._fresh_db()
        try:
            _save(db, self._signal())
            # 같은 (ticker, signal_date, signal_type) 로 두 번째 저장 → update 분기
            _save(db, self._signal(rs=9.9, rs_value=12.5, price=108.0))
            rows = db.query(ScanResult).filter(
                ScanResult.ticker == "TEST",
                ScanResult.signal_date == "2024-06-03",
            ).all()
            assert len(rows) == 1
            assert rows[0].rs_value == 12.5
            assert rows[0].price    == 108.0
        finally:
            db.close()

    def test_save_writes_none_when_rs_value_missing(self):
        from scanner.scan_engine import _save
        from database.models import ScanResult

        db = self._fresh_db()
        try:
            sig = self._signal()
            sig.pop("rs_value")  # Mansfield 미산출 (벤치마크 없음 등)
            _save(db, sig)
            row = db.query(ScanResult).filter(ScanResult.ticker == "TEST").one()
            assert row.rs_value is None  # legacy rs 1.2 가 잘못 기록되지 않아야 함
        finally:
            db.close()
