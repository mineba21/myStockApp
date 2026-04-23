"""Weinstein Stage Analysis Engine  (v2 — Weekly 30-SMA 원전 충실)

v2 업데이트:
  • 주봉 30-SMA + 10-SMA 기반 Stage 판정 (원전 기준)
  • Mansfield RS: (ratio / SMA52(ratio) - 1) * 100  (0선이 기준선)
  • Base Pivot: 5~26주 tight(≤15% 폭) 횡보 → pivot 돌파
  • REBOUND: 시간순(과거→현재) 눌림→반등 탐지
  • analyze_stock 리턴에 weekly/Mansfield 필드 + warning_flags 추가
  • BEAR 장세 Stage4 2중 필터 (analyze + scan_engine)

v3 이하 하위 호환: 기존 테스트가 쓰는 stage_of(), calc_rs(), _build_indicators(),
_signal_quality(), _find_*() 는 legacy wrapper 로 그대로 동작.

신호 유형:
  BREAKOUT   — Stage1→Stage2 base pivot 상향 돌파 (거래량 동반)
  RE_BREAKOUT — Stage2 진행 중 continuation base 돌파
  REBOUND    — Stage2 MA50 눌림목 반등 (지지 확인)
  SELL       — Stage3/4 진입 징후 + 손절 + 기울기 반전
"""
import numpy as np
import pandas as pd
from typing import Optional, Dict, Any, List, Tuple
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    MA_PERIOD, MA_SLOPE_PERIOD, VOLUME_AVG_PERIOD, SCAN_LOOKBACK_DAYS,
    # BREAKOUT
    BREAKOUT_BASE_LOOKBACK_DAYS, BREAKOUT_MIN_BASE_DAYS,
    BREAKOUT_VOLUME_RATIO, BREAKOUT_MAX_EXTENDED_PCT, REQUIRE_PRICE_ABOVE_MA50,
    # RE_BREAKOUT
    REBREAKOUT_BASE_LOOKBACK_DAYS, REBREAKOUT_MAX_PULLBACK_PCT,
    REBREAKOUT_VOLUME_RATIO, REBREAKOUT_REQUIRE_VOLUME_DRYUP,
    # REBOUND
    REBOUND_MA_PERIOD, REBOUND_TOUCH_PCT, REBOUND_CONFIRM_PCT,
    REBOUND_MAX_PULLBACK_PCT, REBOUND_REQUIRE_VOLUME_DRYUP,
)

# v2 신규 파라미터 (backward compat: 없으면 기본값)
try:
    from config import (
        WEEKLY_MA_LONG, WEEKLY_MA_SHORT,
        DAILY_MA_FAST, DAILY_MA_SLOW,
        BREAKOUT_WEEKLY_VOL_RATIO, BREAKOUT_DAILY_VOL_RATIO,
        RS_LOOKBACK_WEEKS, BASE_MIN_WEEKS, PIVOT_LOOKBACK_WEEKS,
        BASE_MAX_WIDTH_PCT, BASE_TIGHT_WIDTH_PCT, MANSFIELD_MIN_RS,
    )
except ImportError:
    WEEKLY_MA_LONG            = 30
    WEEKLY_MA_SHORT           = 10
    DAILY_MA_FAST             = 50
    DAILY_MA_SLOW             = 150
    BREAKOUT_WEEKLY_VOL_RATIO = 2.0
    BREAKOUT_DAILY_VOL_RATIO  = 3.0
    RS_LOOKBACK_WEEKS         = 52
    BASE_MIN_WEEKS            = 5
    PIVOT_LOOKBACK_WEEKS      = 26
    BASE_MAX_WIDTH_PCT        = 15.0
    BASE_TIGHT_WIDTH_PCT      = 8.0
    MANSFIELD_MIN_RS          = 0.0


RS_PERIOD = 65  # 13주(65거래일) 상대강도 — legacy ratio RS용

# Stage 판정 기울기 임계값 (% / bar)
_RISING_SLOPE = 0.05
_FLAT_SLOPE   = 0.02


# ══════════════════════════════════════════════════════════════════
# v2 — 주봉 / Mansfield RS / Base Pivot (신규 공개 API)
# ══════════════════════════════════════════════════════════════════

def _weinstein_mode_flags() -> Tuple[str, bool, bool]:
    """현재 Weinstein v2 모드 플래그를 동적으로 읽는다.

    테스트에서 config 값을 monkeypatch 해도 재import 없이 반영하기 위함이다.
    """
    try:
        import config as cfg
        mode = getattr(cfg, "WEINSTEIN_MODE", "legacy").lower()
        enabled = bool(getattr(cfg, "ENABLE_WEINSTEIN_V2", False)) or mode in ("v2", "strict")
        strict = bool(getattr(cfg, "WEINSTEIN_V2_STRICT", False)) or mode == "strict"
        return mode, enabled, strict
    except Exception:
        return "legacy", False, False

def to_weekly_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """일봉 OHLCV → 주봉 OHLCV (금요일 기준).

    Open=첫날, High=max, Low=min, Close=마지막날, Volume=sum
    """
    if df is None or len(df) < 5:
        return pd.DataFrame()
    idx = pd.to_datetime(df.index)
    weekly = df.copy()
    weekly.index = idx
    agg = weekly.resample("W-FRI").agg({
        "Open":   "first",
        "High":   "max",
        "Low":    "min",
        "Close":  "last",
        "Volume": "sum",
    }).dropna(how="any")
    return agg


def compute_weekly_indicators(weekly_df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """주봉 기반 지표 (30-SMA, 10-SMA, slope)."""
    if weekly_df is None or len(weekly_df) < WEEKLY_MA_LONG:
        return None

    close = weekly_df["Close"]
    vol   = weekly_df["Volume"]
    sma30 = close.rolling(WEEKLY_MA_LONG,  min_periods=WEEKLY_MA_LONG  // 2).mean()
    sma10 = close.rolling(WEEKLY_MA_SHORT, min_periods=WEEKLY_MA_SHORT // 2).mean()
    vol_avg = vol.rolling(10, min_periods=5).mean()

    if pd.isna(sma30.iloc[-1]):
        return None

    cur_close = float(close.iloc[-1])
    cur_sma30 = float(sma30.iloc[-1])
    cur_sma10 = float(sma10.iloc[-1]) if not pd.isna(sma10.iloc[-1]) else cur_close
    cur_vol   = float(vol.iloc[-1])
    cur_volavg = float(vol_avg.iloc[-1]) if not pd.isna(vol_avg.iloc[-1]) else 1.0

    # 30w SMA 기울기 (% per week)
    s30 = sma30.dropna().iloc[-MA_SLOPE_PERIOD:]
    if len(s30) >= 3:
        x = np.arange(len(s30))
        k = np.polyfit(x, s30.values, 1)[0]
        slope30 = float(k / cur_sma30 * 100) if cur_sma30 else 0.0
    else:
        slope30 = 0.0

    return {
        "weekly_df":   weekly_df,
        "weekly_close": close,
        "weekly_vol":  vol,
        "sma30w":      sma30,
        "sma10w":      sma10,
        "cur_close_w": cur_close,
        "cur_sma30w":  cur_sma30,
        "cur_sma10w":  cur_sma10,
        "slope30w":    slope30,
        "cur_vol_w":   cur_vol,
        "cur_volavg_w": cur_volavg,
        "weekly_volume_ratio": round(cur_vol / cur_volavg, 2) if cur_volavg > 0 else 0.0,
    }


def compute_daily_indicators(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """일봉 기반 지표 (MA50, MA150, slope)."""
    return _build_indicators(df)  # legacy 와 동일


def classify_stage(weekly_ind: Optional[Dict], daily_ind: Optional[Dict]) -> str:
    """주봉 30-SMA 기준 Stage 분류 (원전 충실).

      STAGE2: close > sma30w AND slope30w > RISING  (상승 진행)
      STAGE3: close > sma30w AND slope30w ≤ FLAT   (분배/고점)
      STAGE4: close < sma30w AND slope30w < -FLAT  (하락)
      STAGE1: 그 외 (기저 형성)

    weekly_ind 이 None 이면 (데이터 부족) 일봉 기반 legacy 로 fallback.
    """
    if weekly_ind is None:
        if daily_ind is None:
            return "STAGE1"
        return stage_of(daily_ind["cur_p"], daily_ind["cur_m150"], daily_ind["slope150"])

    close  = weekly_ind["cur_close_w"]
    sma30  = weekly_ind["cur_sma30w"]
    sma10  = weekly_ind["cur_sma10w"]
    slope  = weekly_ind["slope30w"]

    above  = close > sma30
    rising = slope >  _RISING_SLOPE
    flat   = -_FLAT_SLOPE <= slope <= _FLAT_SLOPE
    falling = slope < -_FLAT_SLOPE

    # Stage2: 주봉 close > 30w SMA > 10w 도 참고 (강한 상승 추세)
    if above and rising:
        return "STAGE2"
    # Stage3: 30w 위지만 기울기 둔화/평평 (분배)
    if above and (flat or (slope <= _RISING_SLOPE and sma10 < close * 0.98)):
        return "STAGE3"
    # Stage4: 30w 아래 + 하락
    if (not above) and falling:
        return "STAGE4"
    # Stage1: 기저
    return "STAGE1"


def compute_relative_performance(close: pd.Series,
                                 benchmark_close: pd.Series,
                                 lookback_weeks: int = RS_LOOKBACK_WEEKS
                                 ) -> Tuple[Optional[float], Optional[str]]:
    """Mansfield Relative Strength.

    공식: RS_raw = stock / benchmark (가격비)
          Mansfield = (RS_raw[−1] / SMA(RS_raw, 52주) − 1) × 100

    반환:
      (rs_value, rs_trend)
        rs_value: Mansfield RS 값 (>0: 시장 대비 상대 강도, <0: 약함)
        rs_trend: "RISING" / "FALLING" / "FLAT"  (최근 5주 기울기 기준)
    """
    if close is None or benchmark_close is None:
        return None, None
    try:
        stock_weekly = _to_weekly_close(close)
        bench_weekly = _to_weekly_close(benchmark_close)

        # 인덱스 정렬: 공통 날짜만
        aligned = pd.DataFrame({
            "s": stock_weekly.astype(float),
            "b": bench_weekly.astype(float),
        }).dropna()
        if len(aligned) < lookback_weeks:
            return None, None

        ratio = aligned["s"] / aligned["b"].replace(0, np.nan)
        ratio = ratio.dropna()
        if len(ratio) < lookback_weeks:
            return None, None

        sma = ratio.rolling(lookback_weeks, min_periods=lookback_weeks).mean()
        cur_ratio = float(ratio.iloc[-1])
        cur_sma   = float(sma.iloc[-1]) if not pd.isna(sma.iloc[-1]) else None
        if cur_sma is None or cur_sma == 0:
            return None, None

        rs_value = (cur_ratio / cur_sma - 1.0) * 100.0

        # trend: 최근 5주 기울기
        recent = ratio.iloc[-5:]
        if len(recent) >= 5:
            x = np.arange(len(recent))
            k = np.polyfit(x, recent.values, 1)[0]
            rel = k / recent.mean() * 100 if recent.mean() else 0.0
            if rel >   0.1: trend = "RISING"
            elif rel < -0.1: trend = "FALLING"
            else: trend = "FLAT"
        else:
            trend = "FLAT"

        return round(rs_value, 2), trend
    except Exception:
        return None, None


def _to_weekly_close(series: pd.Series) -> pd.Series:
    """가격 series를 Friday close 기준 주봉 close로 변환."""
    if series is None:
        return pd.Series(dtype=float)
    s = series.dropna().astype(float).copy()
    if len(s) == 0:
        return s
    idx = pd.to_datetime(s.index, errors="coerce")
    if pd.isna(idx).any():
        return s.reset_index(drop=True)
    s.index = idx
    if len(idx) >= 2:
        median_days = pd.Series(idx).diff().dropna().dt.days.median()
        if median_days is not None and median_days <= 3:
            return s.resample("W-FRI").last().dropna()
    return s.sort_index()


def _slice_to_signal_date_df(df: pd.DataFrame, signal_date: str) -> pd.DataFrame:
    signal_day = pd.to_datetime(signal_date).date()
    idx = pd.to_datetime(df.index)
    return df.loc[idx.date <= signal_day].copy()


def _slice_to_signal_date_series(series: Optional[pd.Series], signal_date: str) -> Optional[pd.Series]:
    if series is None:
        return None
    signal_day = pd.to_datetime(signal_date).date()
    idx = pd.to_datetime(series.index)
    return series.loc[idx.date <= signal_day].copy()


def detect_base_pivot(df: pd.DataFrame,
                      lookback_weeks: int = PIVOT_LOOKBACK_WEEKS,
                      min_weeks: int = BASE_MIN_WEEKS) -> Optional[Dict[str, Any]]:
    """Base(횡보 압축) 구간과 pivot(고점) 을 탐지.

    조건:
      - 최소 min_weeks 이상 연속 횡보
      - 폭(high_max - low_min)/pivot ≤ BASE_MAX_WIDTH_PCT
      - 현재 bar 는 돌파 후보로 보고 base 계산에서 제외
      - 가장 최근 base 1개만 반환

    df 는 주봉 OHLCV 를 우선으로 받는다. 일봉이 들어오면 호환을 위해
    5거래일≈1주로 환산하되, v2 strict 경로에서는 주봉 df 만 전달한다.

    반환:
      { pivot_price, base_low, base_start_idx, base_end_idx, base_weeks,
        base_width_pct, base_quality: "TIGHT" | "LOOSE" | "WIDE" }
    """
    if df is None or len(df) < 3:
        return None

    high  = df["High"]
    low   = df["Low"]
    n     = len(df)

    idx = pd.to_datetime(df.index)
    if len(idx) >= 2:
        median_days = pd.Series(idx).diff().dropna().dt.days.median()
        rows_per_week = 5 if median_days is not None and median_days <= 3 else 1
    else:
        rows_per_week = 1

    min_rows = max(1, int(min_weeks * rows_per_week))
    lookback_rows = max(min_rows, int(lookback_weeks * rows_per_week))
    if n < min_rows + 1:
        return None

    signal_idx = n - 1
    base_end_idx = signal_idx - 1
    start = max(0, base_end_idx - lookback_rows + 1)

    best = None
    # 뒤에서 앞으로 확장하면서 허용 폭을 유지하는 가장 긴 최신 base를 찾는다.
    pivot_price = float(high.iloc[base_end_idx])
    base_low    = float(low.iloc[base_end_idx])

    for j in range(base_end_idx, start - 1, -1):
        h = float(high.iloc[j])
        l = float(low.iloc[j])
        pivot_price = max(pivot_price, h)
        base_low    = min(base_low, l)
        if pivot_price <= 0:
            continue
        width_pct = (pivot_price - base_low) / pivot_price * 100
        base_rows = base_end_idx - j + 1
        base_weeks = base_rows / rows_per_week

        # 폭이 너무 크면 중단
        if width_pct > BASE_MAX_WIDTH_PCT:
            break

        if base_rows >= min_rows:
            if width_pct <= BASE_TIGHT_WIDTH_PCT:   quality = "TIGHT"
            elif width_pct <= BASE_MAX_WIDTH_PCT: quality = "LOOSE"
            else:                   quality = "WIDE"
            best = {
                "pivot_price":     round(pivot_price, 4),
                "base_low":        round(base_low, 4),
                "base_start_idx":  j,
                "base_end_idx":    base_end_idx,
                "signal_idx":      signal_idx,
                "base_weeks":      round(base_weeks, 1),
                "base_width_pct":  round(width_pct, 2),
                "base_quality":    quality,
            }
    return best


def _daily_vol_ratio(df: pd.DataFrame, idx: int) -> float:
    vol = df["Volume"]
    avg = vol.rolling(VOLUME_AVG_PERIOD, min_periods=10).mean()
    v = float(vol.iloc[idx])
    a = float(avg.iloc[idx]) if not pd.isna(avg.iloc[idx]) else 0.0
    return v / a if a > 0 else 0.0


def _weekly_base_metadata(weekly_ind: Optional[Dict]) -> Optional[Dict[str, Any]]:
    if not weekly_ind:
        return None
    return detect_base_pivot(
        weekly_ind.get("weekly_df"),
        lookback_weeks=PIVOT_LOOKBACK_WEEKS,
        min_weeks=BASE_MIN_WEEKS,
    )


def _attach_base_metadata(sig: Dict[str, Any], base: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not base:
        return sig
    sig["weekly_pivot_price"] = base.get("pivot_price")
    sig["base_weeks"] = base.get("base_weeks")
    sig["base_width_pct"] = base.get("base_width_pct")
    sig["weekly_base_quality"] = base.get("base_quality")
    return sig


def detect_stage2_breakout(df: pd.DataFrame,
                           weekly_ind: Optional[Dict],
                           daily_ind: Optional[Dict]) -> Optional[Dict[str, Any]]:
    """Stage1→Stage2 base pivot 상향 돌파 감지 (legacy-compatible v2 metadata).

    조건:
      - Stage == STAGE1 or STAGE2 (30w SMA 위 또는 근접)
      - 최근 N 일 내 base pivot 상향 돌파
      - 일봉 거래량 ≥ BREAKOUT_DAILY_VOL_RATIO
      - (선택) 주봉 거래량 ≥ BREAKOUT_WEEKLY_VOL_RATIO
      - MA150 대비 과매수 < 15%

    반환: signal dict + warning_flags
    """
    if daily_ind is None:
        return None
    stage = classify_stage(weekly_ind, daily_ind)
    if stage not in ("STAGE1", "STAGE2"):
        return None

    # legacy 로직을 래핑 — 이미 검증된 _find_breakout_signal 사용
    sig = _find_breakout_signal(daily_ind)
    if sig is None:
        return None

    weekly_base = _weekly_base_metadata(weekly_ind)
    sig = _attach_base_metadata(sig, weekly_base)

    warning_flags: List[str] = []
    if weekly_ind is not None:
        wvr = weekly_ind.get("weekly_volume_ratio", 0.0)
        if wvr < BREAKOUT_WEEKLY_VOL_RATIO:
            warning_flags.append(f"약한 주봉 거래량 ({wvr:.1f}x)")
        if stage == "STAGE1":
            warning_flags.append("STAGE1 → 2 전환 (조기 진입)")
        if weekly_base is None:
            warning_flags.append("주봉 base 미확인")

    sig["warning_flags"] = warning_flags
    sig["stage_v2"]      = stage
    return sig


def detect_stage2_breakout_v2(df: pd.DataFrame,
                              weekly_ind: Optional[Dict],
                              daily_ind: Optional[Dict]) -> Optional[Dict[str, Any]]:
    """Strict Weinstein v2 breakout.

    주봉 Stage2, 주봉 base/pivot, 일봉 돌파, 일봉/주봉 거래량을 모두 hard filter로 적용한다.
    """
    if daily_ind is None or weekly_ind is None:
        return None

    if classify_stage(weekly_ind, daily_ind) != "STAGE2":
        return None

    weekly_df = weekly_ind.get("weekly_df")
    weekly_base = _weekly_base_metadata(weekly_ind)
    if weekly_df is None or weekly_base is None:
        return None

    pivot = weekly_base.get("pivot_price")
    if pivot is None or len(weekly_df) < 2:
        return None

    prev_week_close = float(weekly_df["Close"].iloc[-2])
    cur_week_close = float(weekly_df["Close"].iloc[-1])
    if prev_week_close > pivot or cur_week_close <= pivot:
        return None

    sig = _find_breakout_signal(daily_ind)
    if sig is None:
        return None
    signal_close = float(_slice_to_signal_date_df(df, sig["signal_date"])["Close"].iloc[-1])
    if signal_close <= pivot:
        return None

    if sig.get("vol_ratio", 0.0) < BREAKOUT_DAILY_VOL_RATIO:
        return None
    if weekly_ind.get("weekly_volume_ratio", 0.0) < BREAKOUT_WEEKLY_VOL_RATIO:
        return None

    sig = _attach_base_metadata(sig, weekly_base)
    sig["pivot_price"] = weekly_base["pivot_price"]
    sig["warning_flags"] = []
    sig["stage_v2"] = "STAGE2"
    return sig


def detect_continuation_breakout(df: pd.DataFrame,
                                 weekly_ind: Optional[Dict],
                                 daily_ind: Optional[Dict]) -> Optional[Dict[str, Any]]:
    """Stage2 진행 중 continuation base 돌파 감지 (v4)."""
    if daily_ind is None:
        return None
    stage = classify_stage(weekly_ind, daily_ind)
    if stage != "STAGE2":
        return None
    sig = _find_rebreakout_signal(daily_ind)
    if sig is None:
        return None

    warning_flags: List[str] = []
    if weekly_ind and weekly_ind.get("weekly_volume_ratio", 0) < 1.5:
        warning_flags.append("주봉 거래량 감소")
    sig["warning_flags"] = warning_flags
    sig["stage_v2"]      = stage
    return sig


def detect_rebound_entry(df: pd.DataFrame,
                         weekly_ind: Optional[Dict],
                         daily_ind: Optional[Dict]) -> Optional[Dict[str, Any]]:
    """Stage2 MA50 눌림목 반등 감지 (시간순, v4)."""
    if daily_ind is None:
        return None
    stage = classify_stage(weekly_ind, daily_ind)
    if stage not in ("STAGE2",):
        # Stage3 는 legacy 에서 허용했지만 v4 는 엄격히 Stage2만
        # legacy 호환을 위해 Stage3 일봉 허용
        if daily_ind.get("stage") != "STAGE2":
            return None

    sig = _find_rebound_signal(daily_ind)
    if sig is None:
        return None

    warning_flags: List[str] = []
    if weekly_ind:
        if weekly_ind.get("slope30w", 0) <= _FLAT_SLOPE:
            warning_flags.append("주봉 30-SMA 기울기 둔화")
    sig["warning_flags"] = warning_flags
    sig["stage_v2"]      = stage
    return sig


def detect_exit_warning(df: pd.DataFrame,
                        weekly_ind: Optional[Dict],
                        daily_ind: Optional[Dict],
                        buy_price: Optional[float] = None,
                        stop_loss:  Optional[float] = None
                        ) -> Optional[Dict[str, Any]]:
    """Stage3/4 진입, 손절, 30w SMA 이탈 등 종합 매도 경고 (v4).

    기존 check_sell_signal 과 동일한 severity 체계 사용.
    """
    # 현재는 legacy check_sell_signal 을 그대로 사용
    return None  # 상위 API 는 check_sell_signal 을 호출


# ══════════════════════════════════════════════════════════════════
# Legacy 유틸리티 (하위 호환 — 기존 34개 테스트가 의존)
# ══════════════════════════════════════════════════════════════════

def _slope(series: pd.Series, n: int = MA_SLOPE_PERIOD) -> float:
    """MA 기울기(% / bar). 양수 = 상승 추세."""
    s = series.dropna().iloc[-n:]
    if len(s) < max(2, n // 2):
        return 0.0
    x = np.arange(len(s))
    k = np.polyfit(x, s.values, 1)[0]
    cur = s.iloc[-1]
    return float(k / cur * 100) if cur else 0.0


def stage_of(price: float, ma: float, slope: float) -> str:
    """(Legacy) 일봉 MA150 + slope 로 Stage 분류.

    v4 는 classify_stage() 를 사용하지만 호환을 위해 유지.
    """
    up      = price > ma
    rising  = slope >  0.02
    falling = slope < -0.02
    if up and rising:                     return "STAGE2"
    if up and not rising and not falling: return "STAGE3"
    if not up and falling:                return "STAGE4"
    return "STAGE1"


def calc_rs(close: pd.Series, benchmark_close: pd.Series,
            period: int = RS_PERIOD) -> Optional[float]:
    """(Legacy) 단순 ratio RS: 주식수익률 / 지수수익률.

    v4 는 compute_relative_performance() 의 Mansfield RS 를 사용.
    """
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


# ── 지표 빌드 ─────────────────────────────────────────────────────

def _build_indicators(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """공통 일봉 기술적 지표를 계산해 dict로 반환."""
    close   = df["Close"]
    vol     = df["Volume"]
    ma150   = close.rolling(MA_PERIOD,        min_periods=MA_PERIOD // 2).mean()
    ma50    = close.rolling(REBOUND_MA_PERIOD, min_periods=REBOUND_MA_PERIOD // 2).mean()
    vol_avg = vol.rolling(VOLUME_AVG_PERIOD,  min_periods=10).mean()

    if pd.isna(ma150.iloc[-1]):
        return None

    cur_p    = float(close.iloc[-1])
    cur_m150 = float(ma150.iloc[-1])
    cur_m50  = float(ma50.iloc[-1]) if not pd.isna(ma50.iloc[-1]) else cur_p
    sl150    = _slope(ma150)
    sl50     = _slope(ma50)
    cur_v    = float(vol.iloc[-1])
    cur_va   = float(vol_avg.iloc[-1]) if not pd.isna(vol_avg.iloc[-1]) else 1.0

    high = df["High"]
    low  = df["Low"]

    return {
        "close": close, "high": high, "low": low, "vol": vol,
        "ma150": ma150, "ma50": ma50, "vol_avg": vol_avg,
        "cur_p": cur_p, "cur_m150": cur_m150, "cur_m50": cur_m50,
        "slope150": sl150, "slope50": sl50,
        "cur_v": cur_v, "cur_va": cur_va,
        "stage": stage_of(cur_p, cur_m150, sl150),
    }


# ── 시그널 탐지 헬퍼 ──────────────────────────────────────────────

def _find_breakout_signal(ind: Dict) -> Optional[Dict]:
    """Pivot/Base 돌파 감지 (legacy, v3 검증본)."""
    close, high, low = ind["close"], ind["high"], ind["low"]
    vol, vol_avg = ind["vol"], ind["vol_avg"]
    ma150, ma50 = ind["ma150"], ind["ma50"]
    n = len(close)

    for i in range(1, min(SCAN_LOOKBACK_DAYS + 1, n - BREAKOUT_BASE_LOOKBACK_DAYS - 2)):
        abs_i = n - i

        cp   = float(close.iloc[abs_i])
        cm   = float(ma150.iloc[abs_i]) if not pd.isna(ma150.iloc[abs_i]) else None
        cm50 = float(ma50.iloc[abs_i])  if not pd.isna(ma50.iloc[abs_i])  else None
        if cm is None or cm50 is None:
            continue

        if cp <= cm:
            continue
        if _slope(ma150.iloc[:abs_i + 1]) <= 0:
            continue
        if REQUIRE_PRICE_ABOVE_MA50 and cp <= cm50:
            continue

        ext_pct = (cp - cm) / cm * 100
        if ext_pct > BREAKOUT_MAX_EXTENDED_PCT:
            continue

        base_start = abs_i - BREAKOUT_BASE_LOOKBACK_DAYS
        base_end   = abs_i
        if base_start < 0:
            continue
        base_slice = close.iloc[base_start:base_end]
        if len(base_slice) < BREAKOUT_MIN_BASE_DAYS:
            continue

        pivot_high = float(base_slice.max())
        pp = float(close.iloc[abs_i - 1]) if abs_i > 0 else cp
        if pp > pivot_high:
            continue
        if cp <= pivot_high:
            continue

        dv  = float(vol.iloc[abs_i])
        dva = float(vol_avg.iloc[abs_i])
        dvr = dv / dva if dva > 0 else 0.0
        if dvr < BREAKOUT_VOLUME_RATIO:
            continue

        day_high = float(high.iloc[abs_i])
        if day_high > 0 and cp < day_high * 0.70:
            continue

        base_quality = "WEAK"
        pre_len = min(10, abs_i)
        pre_close = close.iloc[abs_i - pre_len:abs_i]
        pre_ma150 = ma150.iloc[abs_i - pre_len:abs_i]
        if pre_len >= 10:
            in_range = sum(
                1 for k in range(pre_len)
                if not pd.isna(pre_ma150.iloc[k]) and pre_ma150.iloc[k] > 0
                and abs(float(pre_close.iloc[k]) - float(pre_ma150.iloc[k]))
                    / float(pre_ma150.iloc[k]) <= 0.05
            )
            if in_range >= 7:
                base_quality = "STRONG"

        return {
            "signal_type":  "BREAKOUT",
            "signal_date":  str(close.index[abs_i].date()),
            "vol_ratio":    round(dvr, 2),
            "pivot_price":  round(pivot_high, 4),
            "support_level": round(cm50, 4),
            "base_quality": base_quality,
        }

    return None


def _find_rebreakout_signal(ind: Dict) -> Optional[Dict]:
    """Stage2 연속 돌파(재돌파) 감지 (legacy)."""
    if ind["stage"] != "STAGE2":
        return None

    close, vol, vol_avg = ind["close"], ind["vol"], ind["vol_avg"]
    ma150, ma50 = ind["ma150"], ind["ma50"]
    n = len(close)

    for i in range(1, min(SCAN_LOOKBACK_DAYS + 1, n - REBREAKOUT_BASE_LOOKBACK_DAYS - 2)):
        abs_i = n - i

        cp   = float(close.iloc[abs_i])
        cm   = float(ma150.iloc[abs_i]) if not pd.isna(ma150.iloc[abs_i]) else None
        cm50 = float(ma50.iloc[abs_i])  if not pd.isna(ma50.iloc[abs_i])  else None
        if cm is None or cm50 is None:
            continue

        if cp <= cm:
            continue
        if cp <= cm50:
            continue
        if _slope(ma150.iloc[:abs_i + 1]) <= 0:
            continue

        base_start = abs_i - REBREAKOUT_BASE_LOOKBACK_DAYS
        base_end   = abs_i
        if base_start < 0:
            continue
        base_slice = close.iloc[base_start:base_end]
        if len(base_slice) < 5:
            continue

        pivot_high = float(base_slice.max())
        pivot_low  = float(base_slice.min())

        pullback_pct = (pivot_high - pivot_low) / pivot_high * 100
        if pullback_pct < 3.0:
            continue
        if pullback_pct > REBREAKOUT_MAX_PULLBACK_PCT:
            continue

        pp = float(close.iloc[abs_i - 1]) if abs_i > 0 else cp
        if pp > pivot_high:
            continue
        if cp <= pivot_high:
            continue

        dv  = float(vol.iloc[abs_i])
        dva = float(vol_avg.iloc[abs_i])
        dvr = dv / dva if dva > 0 else 0.0
        if dvr < REBREAKOUT_VOLUME_RATIO:
            continue

        if REBREAKOUT_REQUIRE_VOLUME_DRYUP:
            base_vol     = vol.iloc[base_start:base_end]
            base_vol_avg = vol_avg.iloc[base_start:base_end]
            valid_mask   = base_vol_avg > 0
            if valid_mask.any():
                avg_ratio = float((base_vol[valid_mask] / base_vol_avg[valid_mask]).mean())
                if avg_ratio > 0.8:
                    continue

        return {
            "signal_type":  "RE_BREAKOUT",
            "signal_date":  str(close.index[abs_i].date()),
            "vol_ratio":    round(dvr, 2),
            "pivot_price":  round(pivot_high, 4),
            "support_level": round(cm50, 4),
        }

    return None


def _find_rebound_signal(ind: Dict) -> Optional[Dict]:
    """MA50 눌림목 반등 감지 (시간순, legacy)."""
    if ind["stage"] not in ("STAGE2", "STAGE3"):
        return None
    if ind["slope150"] <= 0.02:
        return None

    close, low, vol, vol_avg = ind["close"], ind["low"], ind["vol"], ind["vol_avg"]
    ma150, ma50 = ind["ma150"], ind["ma50"]
    n = len(close)

    scan_len   = min(SCAN_LOOKBACK_DAYS + 20, n - 2)
    win_start  = n - scan_len

    touched_low  = None
    touch_ma50   = None
    latest_sig   = None

    for j in range(win_start, n - 1):
        p   = float(close.iloc[j])
        l   = float(low.iloc[j])
        if pd.isna(ma50.iloc[j]) or pd.isna(ma150.iloc[j]):
            continue

        m50  = float(ma50.iloc[j])
        m150 = float(ma150.iloc[j])

        if p < m150 * 0.95:
            touched_low = None
            touch_ma50  = None
            continue

        if touched_low is None:
            touch_limit = m50 * (1.0 + REBOUND_TOUCH_PCT / 100)
            max_pullback = m50 * (1.0 - REBOUND_MAX_PULLBACK_PCT / 100)
            if max_pullback <= l <= touch_limit:
                touched_low = l
                touch_ma50  = m50
        else:
            if l < touched_low:
                touched_low = l
            if m50 > 0 and l < m50 * (1.0 - REBOUND_MAX_PULLBACK_PCT / 100):
                touched_low = None
                touch_ma50  = None
                continue

            if (touched_low and
                p >= touched_low * (1.0 + REBOUND_CONFIRM_PCT / 100) and
                p > m50):

                dv  = float(vol.iloc[j])
                dva = float(vol_avg.iloc[j])
                dvr = dv / dva if dva > 0 else 0.0
                if dvr < 1.3:
                    touched_low = None
                    touch_ma50  = None
                    continue

                days_ago = (n - 1) - j
                if days_ago < SCAN_LOOKBACK_DAYS:
                    latest_sig = {
                        "signal_type":   "REBOUND",
                        "signal_date":   str(close.index[j].date()),
                        "vol_ratio":     round(dvr, 2),
                        "support_level": round(touch_ma50, 4) if touch_ma50 else round(m50, 4),
                        "pivot_price":   None,
                    }
                touched_low = None
                touch_ma50  = None

    return latest_sig


# ── 신호 품질 계산 ─────────────────────────────────────────────────

def _signal_quality(vol_ratio: float, slope: float, rs: Optional[float],
                    signal_type: str) -> str:
    """STRONG / MODERATE / WEAK 품질 점수 (legacy)."""
    score = 0
    if vol_ratio >= 3.0:  score += 2
    elif vol_ratio >= 2.0: score += 1

    if slope > 0.10:  score += 2
    elif slope > 0.04: score += 1

    if rs is not None:
        if rs >= 1.5:  score += 2
        elif rs >= 1.0: score += 1

    if signal_type == "BREAKOUT": score += 1

    if score >= 5: return "STRONG"
    if score >= 3: return "MODERATE"
    return "WEAK"


def _passes_v2_strict_filters(sig: Dict[str, Any],
                              weekly_stage: str,
                              weekly_ind: Optional[Dict],
                              mansfield_rs: Optional[float]) -> Tuple[bool, List[str]]:
    """Strict v2 hard filters for buy signals."""
    reasons: List[str] = []

    if weekly_stage != "STAGE2":
        reasons.append(f"weekly_stage={weekly_stage}")

    if mansfield_rs is None or mansfield_rs <= MANSFIELD_MIN_RS:
        reasons.append("Mansfield RS <= 0")

    sig_type = sig.get("signal_type")
    daily_min = {
        "BREAKOUT": BREAKOUT_DAILY_VOL_RATIO,
        "RE_BREAKOUT": REBREAKOUT_VOLUME_RATIO,
        "REBOUND": 1.3,
    }.get(sig_type, BREAKOUT_VOLUME_RATIO)
    if sig.get("vol_ratio", 0.0) < daily_min:
        reasons.append(f"daily volume < {daily_min:.1f}x")

    weekly_vol = weekly_ind.get("weekly_volume_ratio") if weekly_ind else None
    if weekly_vol is None or weekly_vol < BREAKOUT_WEEKLY_VOL_RATIO:
        reasons.append(f"weekly volume < {BREAKOUT_WEEKLY_VOL_RATIO:.1f}x")

    if sig_type == "BREAKOUT":
        if sig.get("base_weeks") is None or sig.get("base_width_pct") is None:
            reasons.append("weekly base missing")

    return not reasons, reasons


def _strict_signal_context(df: pd.DataFrame,
                           sig: Dict[str, Any],
                           benchmark_close: Optional[pd.Series]) -> Optional[Dict[str, Any]]:
    """signal_date 시점까지만 사용해 strict v2 context를 재계산한다."""
    signal_date = sig.get("signal_date")
    if not signal_date:
        return None

    df_at_signal = _slice_to_signal_date_df(df, signal_date)
    if df_at_signal is None or len(df_at_signal) == 0:
        return None

    daily_ind = _build_indicators(df_at_signal)
    if daily_ind is None:
        return None

    weekly_df = to_weekly_ohlcv(df_at_signal)
    weekly_ind = compute_weekly_indicators(weekly_df) if len(weekly_df) > 0 else None
    weekly_stage = classify_stage(weekly_ind, daily_ind)

    bench_at_signal = _slice_to_signal_date_series(benchmark_close, signal_date)
    mansfield_rs, rs_trend = (None, None)
    rs_legacy = None
    if bench_at_signal is not None:
        mansfield_rs, rs_trend = compute_relative_performance(
            daily_ind["close"], bench_at_signal, lookback_weeks=RS_LOOKBACK_WEEKS
        )
        rs_legacy = calc_rs(daily_ind["close"], bench_at_signal)

    if sig.get("signal_type") == "BREAKOUT":
        strict_sig = detect_stage2_breakout_v2(df_at_signal, weekly_ind, daily_ind)
        if strict_sig is None or strict_sig.get("signal_date") != signal_date:
            return None
        sig = {**sig, **strict_sig}

    passed, _ = _passes_v2_strict_filters(sig, weekly_stage, weekly_ind, mansfield_rs)
    if not passed:
        return None

    return {
        "sig": sig,
        "daily_ind": daily_ind,
        "weekly_ind": weekly_ind,
        "weekly_stage": weekly_stage,
        "mansfield_rs": mansfield_rs,
        "rs_trend": rs_trend,
        "rs_legacy": rs_legacy,
    }


# ══════════════════════════════════════════════════════════════════
# 공개 API — analyze_stock / check_sell_signal
# ══════════════════════════════════════════════════════════════════

def analyze_stock(df: pd.DataFrame, ticker: str, name: str, market: str,
                  benchmark_close: pd.Series = None,
                  market_condition: str = None) -> Optional[dict]:
    """주식 하나에 대해 Weinstein 매수 시그널을 탐지.

    반환 dict:
      기존 필드: ticker, name, market, signal_type, stage, price, ma150, ma50,
                price_vs_ma_pct, ma_slope, volume, volume_avg, volume_ratio,
                signal_date, rs, pivot_price, support_level, base_quality,
                market_condition, signal_quality, rs_passed
      v2 신규: sma30w, sma10w, weekly_stage, mansfield_rs,
              rs_trend, weekly_volume_ratio, base_weeks, warning_flags
    """
    if df is None or len(df) < MA_PERIOD + BREAKOUT_BASE_LOOKBACK_DAYS + 10:
        return None

    df = df.copy().sort_index()
    _, _, strict_v2 = _weinstein_mode_flags()

    # ── v2: 주봉 + 일봉 indicator 병렬 계산 ──
    daily_ind  = _build_indicators(df)
    if daily_ind is None:
        return None

    weekly_df  = to_weekly_ohlcv(df)
    weekly_ind = compute_weekly_indicators(weekly_df) if len(weekly_df) > 0 else None
    weekly_stage = classify_stage(weekly_ind, daily_ind)

    # ── v2: BEAR 장세에서 Stage4 는 1차 차단 (scan_engine 필터와 2중) ──
    if market_condition == "BEAR" and weekly_stage == "STAGE4":
        return None

    strict_context = None

    # 시그널 탐지 — legacy baseline 유지, strict 모드에서는 signal_date 기준으로 재검증
    if strict_v2:
        sig = (
            _find_breakout_signal(daily_ind)
            or _find_rebreakout_signal(daily_ind)
            or _find_rebound_signal(daily_ind)
        )
        if sig is not None:
            strict_context = _strict_signal_context(df, sig, benchmark_close)
            if strict_context is None:
                return None
            sig = strict_context["sig"]
    else:
        sig = (
            detect_stage2_breakout(df, weekly_ind, daily_ind)
            or detect_continuation_breakout(df, weekly_ind, daily_ind)
            or detect_rebound_entry(df, weekly_ind, daily_ind)
        )
    if sig is None:
        return None

    cur_p    = daily_ind["cur_p"]
    cur_m150 = daily_ind["cur_m150"]
    cur_v    = daily_ind["cur_v"]
    cur_va   = daily_ind["cur_va"]
    slope    = daily_ind["slope150"]

    pct = (cur_p - cur_m150) / cur_m150 * 100 if cur_m150 else 0.0

    # ── Mansfield RS (v2) + legacy ratio RS ──
    mansfield_rs, rs_trend = (None, None)
    rs_legacy = None
    if benchmark_close is not None:
        mansfield_rs, rs_trend = compute_relative_performance(
            daily_ind["close"], benchmark_close, lookback_weeks=RS_LOOKBACK_WEEKS
        )
        rs_legacy = calc_rs(daily_ind["close"], benchmark_close)

    if strict_v2:
        weekly_ind = strict_context["weekly_ind"]
        weekly_stage = strict_context["weekly_stage"]
        mansfield_rs = strict_context["mansfield_rs"]
        rs_trend = strict_context["rs_trend"]
        rs_legacy = strict_context["rs_legacy"]

    # signal_quality 는 legacy ratio RS 로 계산 (기존 테스트 호환)
    qual = _signal_quality(sig["vol_ratio"], slope, rs_legacy, sig["signal_type"])

    # warning_flags 축적
    warning_flags: List[str] = list(sig.get("warning_flags") or [])
    if mansfield_rs is not None and mansfield_rs < 0:
        warning_flags.append(f"Mansfield RS < 0 ({mansfield_rs:+.1f})")
    if rs_trend == "FALLING":
        warning_flags.append("RS 하락 추세")

    result = {
        "ticker":          ticker,
        "name":            name,
        "market":          market,
        "signal_type":     sig["signal_type"],
        "stage":           daily_ind["stage"],       # legacy — 일봉 기준
        "weekly_stage":    weekly_stage,             # v2 — 주봉 기준
        "price":           round(cur_p, 4),
        "ma150":           round(cur_m150, 4),
        "ma50":            round(daily_ind["cur_m50"], 4),
        "price_vs_ma_pct": round(pct, 2),
        "ma_slope":        round(slope, 4),
        "volume":          int(cur_v),
        "volume_avg":      int(cur_va),
        "volume_ratio":    sig["vol_ratio"],
        "signal_date":     sig["signal_date"],
        "rs":              rs_legacy,                # legacy ratio RS
        "rs_value":        rs_legacy,                # DB/API legacy alias
        "mansfield_rs":    mansfield_rs,             # v2 Mansfield RS
        "rs_trend":        rs_trend,
        "pivot_price":     sig.get("pivot_price"),
        "support_level":   sig.get("support_level"),
        "base_quality":    sig.get("base_quality", "N/A"),
        "base_weeks":      sig.get("base_weeks"),
        "base_width_pct":  sig.get("base_width_pct"),
        "weekly_base_quality": sig.get("weekly_base_quality"),
        "market_condition": market_condition,
        "signal_quality":  qual,
        "rs_passed":       (rs_legacy is not None and rs_legacy >= 1.0),
        "mansfield_rs_passed": (mansfield_rs is not None and mansfield_rs > MANSFIELD_MIN_RS),
        "warning_flags":   warning_flags,
    }
    if weekly_ind is not None:
        result["sma30w"] = round(weekly_ind["cur_sma30w"], 4)
        result["sma10w"] = round(weekly_ind["cur_sma10w"], 4)
        result["weekly_volume_ratio"] = weekly_ind.get("weekly_volume_ratio")
    return result


def check_sell_signal(df: pd.DataFrame, ticker: str, name: str, market: str,
                      buy_price: float = None, stop_loss: float = None) -> Optional[dict]:
    """감시 종목 매도 시그널 체크 (severity: HIGH / MEDIUM / LOW)."""
    if df is None or len(df) < MA_PERIOD + 20:
        return None

    df    = df.copy().sort_index()
    close = df["Close"]
    ma    = close.rolling(MA_PERIOD, min_periods=MA_PERIOD // 2).mean()

    cur_p  = float(close.iloc[-1])
    cur_ma = float(ma.iloc[-1])
    slope  = _slope(ma)
    stage  = stage_of(cur_p, cur_ma, slope)

    reason   = None
    severity = None

    if stop_loss and cur_p <= stop_loss:
        reason   = f"손절가 도달 (현재 {cur_p:,.0f} ≤ 손절 {stop_loss:,.0f})"
        severity = "HIGH"

    elif stage == "STAGE4":
        for i in range(1, 4):
            pp = float(close.iloc[-i - 1])
            pm = float(ma.iloc[-i - 1]) if not pd.isna(ma.iloc[-i - 1]) else None
            if pm and pp > pm and cur_p < cur_ma:
                reason   = "MA150 하향 이탈 (Stage4 진입)"
                severity = "HIGH"
                break

    if reason is None and len(ma.dropna()) >= 6:
        slope_past = _slope(ma.iloc[:-5], n=MA_SLOPE_PERIOD)
        if slope_past > 0 and slope <= 0:
            reason   = "MA150 기울기 반전 (상승 추세 약화)"
            severity = "MEDIUM"

    if reason is None and stage == "STAGE3":
        reason   = "Stage3 진입 징후 (고점 부근, 분배 주의)"
        severity = "LOW"

    if reason is None:
        return None

    return {
        "ticker":      ticker,
        "name":        name,
        "market":      market,
        "signal_type": "SELL",
        "stage":       stage,
        "price":       round(cur_p, 4),
        "ma150":       round(cur_ma, 4),
        "ma_slope":    round(slope, 4),
        "sell_reason": reason,
        "severity":    severity,
        "buy_price":   buy_price,
        "profit_pct":  round((cur_p - buy_price) / buy_price * 100, 2) if buy_price else None,
    }
