"""Weinstein Stage Analysis Engine"""
import numpy as np
import pandas as pd
from typing import Optional
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import MA_PERIOD, MA_SLOPE_PERIOD, VOLUME_SURGE_RATIO, VOLUME_AVG_PERIOD, SCAN_LOOKBACK_DAYS

REBREAKOUT_HIGH_DAYS = 20   # 재돌파: 최근 N일 고점 기준
REBREAKOUT_VOL_RATIO = 2.0  # 재돌파: 거래량 2배 이상
RS_PERIOD = 65              # 상대강도: 13주(65거래일)


def _slope(series: pd.Series, n: int = MA_SLOPE_PERIOD) -> float:
    s = series.iloc[-n:].dropna()
    if len(s) < n // 2:
        return 0.0
    x = np.arange(len(s))
    k = np.polyfit(x, s.values, 1)[0]
    cur = s.iloc[-1]
    return (k / cur * 100) if cur else 0.0


def stage_of(price: float, ma: float, slope: float) -> str:
    up = price > ma
    rising  = slope >  0.02
    falling = slope < -0.02
    if up and rising:                    return "STAGE2"
    if up and not rising and not falling: return "STAGE3"
    if not up and falling:               return "STAGE4"
    return "STAGE1"


def calc_rs(close: pd.Series, benchmark_close: pd.Series, period: int = RS_PERIOD) -> Optional[float]:
    """상대강도(RS): 주식 N일 수익률 ÷ 지수 N일 수익률. >1 = 시장 아웃퍼폼"""
    try:
        if len(close) < period or len(benchmark_close) < period:
            return None
        stock_ret = float(close.iloc[-1]) / float(close.iloc[-period]) - 1
        bench_ret = float(benchmark_close.iloc[-1]) / float(benchmark_close.iloc[-period]) - 1
        if bench_ret == 0:
            return None
        return round(stock_ret / bench_ret, 2)
    except Exception:
        return None


def analyze_stock(df: pd.DataFrame, ticker: str, name: str, market: str,
                  benchmark_close: pd.Series = None) -> Optional[dict]:
    if df is None or len(df) < MA_PERIOD + 20:
        return None

    df    = df.copy().sort_index()
    close = df["Close"]
    vol   = df["Volume"]
    ma    = close.rolling(MA_PERIOD, min_periods=MA_PERIOD // 2).mean()
    ma50  = close.rolling(50, min_periods=25).mean()
    va    = vol.rolling(VOLUME_AVG_PERIOD, min_periods=10).mean()

    if pd.isna(ma.iloc[-1]):
        return None

    cur_p  = float(close.iloc[-1])
    cur_ma = float(ma.iloc[-1])
    cur_v  = float(vol.iloc[-1])
    cur_va = float(va.iloc[-1])
    slope  = _slope(ma)
    stage  = stage_of(cur_p, cur_ma, slope)
    vr     = (cur_v / cur_va) if cur_va > 0 else 0.0

    signal_type = None
    signal_date = None
    vol_ratio   = vr
    base_quality = "NONE"

    lb_close = close.iloc[-(SCAN_LOOKBACK_DAYS + 5):]
    lb_ma    = ma.iloc[-(SCAN_LOOKBACK_DAYS + 5):]
    lb_ma50  = ma50.iloc[-(SCAN_LOOKBACK_DAYS + 5):]
    lb_vol   = vol.iloc[-(SCAN_LOOKBACK_DAYS + 5):]
    lb_va    = va.iloc[-(SCAN_LOOKBACK_DAYS + 5):]
    lb_high  = df["High"].iloc[-(SCAN_LOOKBACK_DAYS + 5):]
    lb_low   = df["Low"].iloc[-(SCAN_LOOKBACK_DAYS + 5):]

    # ── BREAKOUT: MA 상향 돌파 (Stage1→2 전환) + 거래량 급증 ──
    for i in range(1, min(SCAN_LOOKBACK_DAYS + 1, len(lb_close))):
        pp = float(lb_close.iloc[-i - 1])
        cp = float(lb_close.iloc[-i])
        pm = float(lb_ma.iloc[-i - 1]) if not pd.isna(lb_ma.iloc[-i - 1]) else None
        cm = float(lb_ma.iloc[-i])     if not pd.isna(lb_ma.iloc[-i])     else None
        if pm is None or cm is None:
            continue
        if pp <= pm and cp > cm:
            dv  = float(lb_vol.iloc[-i])
            dva = float(lb_va.iloc[-i])
            dvr = (dv / dva) if dva > 0 else 0.0
            base_ok = False
            close_high_ok = False
            try:
                # Validate base behavior on the 10 sessions before breakout day.
                bidx = len(lb_close) - i
                if bidx - 10 >= 0:
                    base_close = lb_close.iloc[bidx - 10:bidx]
                    base_ma150 = lb_ma.iloc[bidx - 10:bidx]
                    in_range = ((base_close >= base_ma150 * 0.95) &
                                (base_close <= base_ma150 * 1.05))
                    base_ok = int(in_range.fillna(False).sum()) >= 7

                # Filter weak closes near intraday low on breakout day.
                day_high = float(lb_high.iloc[-i])
                close_high_ok = (cp / day_high) >= 0.70 if day_high > 0 else False
            except Exception:
                base_ok = False
                close_high_ok = False

            if dvr >= VOLUME_SURGE_RATIO and base_ok and close_high_ok:
                signal_type = "BREAKOUT"
                signal_date = str(lb_close.index[-i].date())
                vol_ratio   = dvr
                if dvr >= 2.0:
                    base_quality = "STRONG"
                elif dvr >= 1.5:
                    base_quality = "WEAK"
                else:
                    base_quality = "NONE"
                break

    # ── RE_BREAKOUT: Stage2 진행 중 최근 20일 고점 재돌파 + 거래량 2배 ──
    # (조정·횡보 후 이전 고점을 다시 뚫는 '추세 지속 매매')
    if signal_type is None and stage == "STAGE2":
        # shift(1).rolling(N).max() = 해당 날짜 기준 이전 N일간 최고가
        high_20 = close.shift(1).rolling(REBREAKOUT_HIGH_DAYS).max()
        lb_h20  = high_20.iloc[-(SCAN_LOOKBACK_DAYS + 5):]
        for i in range(1, min(SCAN_LOOKBACK_DAYS + 1, len(lb_close))):
            h20 = lb_h20.iloc[-i]
            if pd.isna(h20):
                continue
            h20 = float(h20)
            cp  = float(lb_close.iloc[-i])
            pp  = float(lb_close.iloc[-i - 1]) if i + 1 <= len(lb_close) else cp
            if pp <= h20 and cp > h20:          # 20일 고점 돌파
                dv  = float(lb_vol.iloc[-i])
                dva = float(lb_va.iloc[-i])
                dvr = (dv / dva) if dva > 0 else 0.0
                if dvr >= REBREAKOUT_VOL_RATIO:
                    signal_type = "RE_BREAKOUT"
                    signal_date = str(lb_close.index[-i].date())
                    vol_ratio   = dvr
                    break

    # ── REBOUND: Stage2 눌림목(MA ±3%) 후 반등 ──
    if signal_type is None and stage == "STAGE2":
        try:
            touched = False
            max_i = min(SCAN_LOOKBACK_DAYS + 1, len(lb_close))
            for i in range(max_i - 1, 0, -1):
                p = float(lb_close.iloc[-i])
                m50 = float(lb_ma50.iloc[-i]) if not pd.isna(lb_ma50.iloc[-i]) else None
                lo = float(lb_low.iloc[-i])
                dv = float(lb_vol.iloc[-i])
                dva = float(lb_va.iloc[-i])
                dvr = (dv / dva) if dva > 0 else 0.0
                if m50 is None:
                    continue
                if lo <= m50 * 0.97:
                    touched = True
                if touched and p > m50 and dvr >= 1.3 and slope > 0.02:
                    signal_type = "REBOUND"
                    signal_date = str(lb_close.index[-i].date())
                    vol_ratio = dvr
                    break
        except Exception:
            pass

    if signal_type is None:
        return None

    if signal_type in ("RE_BREAKOUT", "REBOUND"):
        base_quality = "N/A"

    pct = ((cur_p - cur_ma) / cur_ma * 100) if cur_ma else 0.0
    rs  = calc_rs(close, benchmark_close) if benchmark_close is not None else None

    return {
        "ticker": ticker, "name": name, "market": market,
        "signal_type": signal_type, "stage": stage,
        "price": round(cur_p, 4), "ma150": round(cur_ma, 4),
        "ma50": round(float(ma50.iloc[-1]), 4) if not pd.isna(ma50.iloc[-1]) else 0.0,
        "price_vs_ma_pct": round(pct, 2), "ma_slope": round(slope, 4),
        "volume": int(cur_v), "volume_avg": int(cur_va),
        "volume_ratio": round(vol_ratio, 2),
        "base_quality": base_quality,
        "signal_date": signal_date,
        "rs": rs,  # 상대강도 (>1: 시장 아웃퍼폼)
    }


def check_sell_signal(df: pd.DataFrame, ticker: str, name: str, market: str,
                      buy_price: float = None, stop_loss: float = None) -> Optional[dict]:
    if df is None or len(df) < MA_PERIOD + 20:
        return None

    df    = df.copy().sort_index()
    close = df["Close"]
    ma    = close.rolling(MA_PERIOD, min_periods=MA_PERIOD // 2).mean()

    cur_p  = float(close.iloc[-1])
    cur_ma = float(ma.iloc[-1])
    slope  = _slope(ma)
    stage  = stage_of(cur_p, cur_ma, slope)

    reason = None
    severity = "LOW"
    ma_break = False
    slope_turn = False
    try:
        for i in range(1, min(4, len(close) - 1)):
            pp = float(close.iloc[-i - 1])
            pm = float(ma.iloc[-i - 1]) if not pd.isna(ma.iloc[-i - 1]) else None
            if pm and pp > pm and cur_p < cur_ma:
                ma_break = True
                break
    except Exception:
        ma_break = False

    try:
        prev5_slope = _slope(ma.iloc[:-5]) if len(ma) > (MA_SLOPE_PERIOD + 5) else 0.0
        slope_turn = prev5_slope > 0 and slope <= 0
    except Exception:
        slope_turn = False

    if stop_loss and cur_p <= stop_loss:
        reason = f"손절가 도달 (현재 {cur_p:,.0f} ≤ 손절 {stop_loss:,.0f})"
        severity = "HIGH"
    elif stage == "STAGE4" and ma_break:
        reason = "MA 하향 이탈 (Stage4 진입)"
        severity = "HIGH"
    elif stage == "STAGE3":
        if ma_break:
            reason = "MA150 하향 이탈 (Stage3)"
            severity = "HIGH"
        elif slope_turn:
            reason = "MA150 기울기 양→음 전환 (Stage3)"
            severity = "MEDIUM"
        else:
            reason = "Stage3 진입 주의 (고점 부근, 분배 징후)"
            severity = "LOW"

    if reason is None:
        return None

    return {
        "ticker": ticker, "name": name, "market": market,
        "signal_type": "SELL", "stage": stage,
        "price": round(cur_p, 4), "ma150": round(cur_ma, 4),
        "ma_slope": round(slope, 4), "sell_reason": reason,
        "severity": severity,
        "buy_price": buy_price,
        "profit_pct": round((cur_p - buy_price) / buy_price * 100, 2) if buy_price else None,
    }
