"""스캔 엔진 - 전체 스캔 오케스트레이션"""
import logging
from datetime import datetime
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

scan_status = {
    "is_running": False, "market": "",
    "progress": 0, "total": 0,
    "current_stock": "", "started_at": None,
}


def _prog(cur, tot, msg=""):
    scan_status.update(progress=cur, total=tot, current_stock=msg)


# ── 시장 필터 ─────────────────────────────────────────────────────

def _get_market_filter_decision(market_condition: Optional[str],
                                signal_type: str) -> Tuple[bool, Optional[str]]:
    """
    시장 상태에 따라 BUY 시그널 허용 여부를 결정합니다.

    반환값: (allow: bool, flag_msg: str | None)
      - allow=False → 시그널을 저장/알림하지 않음
      - flag_msg    → 허용되지만 주의 메시지 있음 (CAUTION 상황)
    """
    try:
        from config import ENABLE_MARKET_FILTER, BLOCK_NEW_BUYS_IN_BEAR, CAUTION_MODE
    except ImportError:
        return True, None

    if not ENABLE_MARKET_FILTER or market_condition is None:
        return True, None

    if BLOCK_NEW_BUYS_IN_BEAR and market_condition == "BEAR":
        return False, "BEAR 장세 필터"

    if market_condition == "CAUTION":
        if CAUTION_MODE == "block_breakout" and signal_type == "BREAKOUT":
            return False, "CAUTION: 돌파 차단"
        elif CAUTION_MODE == "allow_with_flag":
            return True, "⚠️ CAUTION 장세"
        # allow_all: 아무것도 차단하지 않음

    return True, None


# ── 등급 계산 ─────────────────────────────────────────────────────

def _grade(signal: dict) -> str:
    """
    S / A / B 종합 등급.

    점수 기준:
      signal_quality STRONG=3 / MODERATE=2 / WEAK=1
      signal_type    BREAKOUT +1
      base_quality   STRONG   +1
      rs             ≥1.5 +1  / ≥1.0 +0.5
      시장 조건      BULL +1  / BEAR -2

    등급: S(≥6) / A(≥4) / B(나머지)
    """
    qual         = signal.get("signal_quality", "WEAK")
    signal_type  = signal.get("signal_type", "")
    base_quality = signal.get("base_quality", "N/A")
    rs           = signal.get("rs")
    mkt          = signal.get("market_condition", "")

    score = {"STRONG": 3, "MODERATE": 2, "WEAK": 1}.get(qual, 1)

    if signal_type == "BREAKOUT":    score += 1
    if base_quality == "STRONG":     score += 1
    if rs is not None:
        if rs >= 1.5:   score += 1
        elif rs >= 1.0: score += 0.5

    if mkt == "BULL":   score += 1
    elif mkt == "BEAR": score -= 2

    if score >= 6: return "S"
    if score >= 4: return "A"
    return "B"


# ── 메인 스캔 ─────────────────────────────────────────────────────

def run_scan(market: str = "ALL", universe: str = None,
             triggered_by: str = "manual") -> dict:
    if scan_status["is_running"]:
        return {"status": "already_running"}

    scan_status.update(is_running=True, market=market,
                       progress=0, total=0, started_at=datetime.now().isoformat())

    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from database.models import SessionLocal, ScanResult, ScanLog
    from scanner.weinstein import analyze_stock, check_sell_signal
    from notifications.telegram import send_telegram_message
    from config import US_UNIVERSE

    # universe 파싱: KR 유니버스(kospi/kosdaq/kospi+kosdaq) vs US 유니버스 구분
    KR_UNIVERSES = {"kospi", "kosdaq", "kospi+kosdaq"}
    if universe and universe.lower() in KR_UNIVERSES:
        kr_universe = universe.lower()
        us_universe = US_UNIVERSE
    else:
        kr_universe = "kospi+kosdaq"
        us_universe = universe if universe else US_UNIVERSE

    db  = SessionLocal()
    log = ScanLog(market=market, triggered_by=triggered_by, status="RUNNING")
    db.add(log); db.commit(); db.refresh(log)

    buy_signals, total_scanned = [], 0

    try:
        # 시장 지수 상태 로드 (Forest to Trees)
        from scanner.market_analysis import get_market_stages, get_benchmark_close
        market_stages = get_market_stages()
        kr_condition  = market_stages.get("KR_condition")
        us_condition  = market_stages.get("US_condition")

        # 벤치마크 로드 (RS 계산용)
        kr_bench = get_benchmark_close("KR") if market in ("KR", "ALL") else None
        us_bench = get_benchmark_close("US") if market in ("US", "ALL") else None

        if market in ("KR", "ALL"):
            sigs, cnt = _scan_kr(db, kr_bench, kr_condition, kr_universe)
            buy_signals.extend(sigs); total_scanned += cnt

        if market in ("US", "ALL"):
            sigs, cnt = _scan_us(db, us_universe, us_bench, us_condition)
            buy_signals.extend(sigs); total_scanned += cnt

        sell_signals = _check_watchlist(db, kr_bench=kr_bench, us_bench=us_bench)

        if buy_signals or sell_signals:
            _notify(buy_signals, sell_signals, send_telegram_message)

        log.finished_at   = datetime.utcnow()
        log.total_scanned = total_scanned
        log.signals_found = len(buy_signals)
        log.status        = "DONE"
        db.commit()

        return {"status": "done", "total_scanned": total_scanned,
                "signals_found": len(buy_signals),
                "sell_signals": len(sell_signals)}

    except Exception as e:
        logger.error(f"스캔 오류: {e}", exc_info=True)
        log.status    = "ERROR"
        log.error_msg = str(e)
        log.finished_at = datetime.utcnow()
        db.commit()
        return {"status": "error", "message": str(e)}

    finally:
        scan_status["is_running"] = False
        db.close()


def _scan_kr(db, benchmark_close=None, market_condition=None, kr_universe="kospi+kosdaq"):
    from scanner.kr_stocks import get_all_kr_tickers, get_kr_ohlcv
    from scanner.weinstein import analyze_stock
    import time

    tickers = get_all_kr_tickers(market_filter=kr_universe)
    signals, count = [], 0

    for i, info in enumerate(tickers):
        _prog(i + 1, len(tickers), f"KR [{i+1}/{len(tickers)}] {info['name']}")
        df = get_kr_ohlcv(info["ticker"])
        if df is None:
            continue
        res = analyze_stock(df, info["ticker"], info["name"], "KR",
                            benchmark_close, market_condition)
        count += 1
        if res:
            allow, flag = _get_market_filter_decision(market_condition, res["signal_type"])
            if not allow:
                logger.debug(f"[KR] {info['ticker']} 필터됨: {flag}")
                continue
            if flag:
                res["_market_flag"] = flag
            _save(db, res)
            signals.append(res)
            logger.info(f"[KR] {info['ticker']} {info['name']}: {res['signal_type']} "
                        f"Q={res.get('signal_quality','?')}")
        time.sleep(0.05)

    return signals, count


def _scan_us(db, universe, benchmark_close=None, market_condition=None):
    from scanner.us_stocks import get_all_us_tickers, get_us_batch
    from scanner.weinstein import analyze_stock

    tickers = get_all_us_tickers(universe)
    results = get_us_batch(tickers, progress_callback=_prog)
    signals, count = [], 0

    for info, df in results:
        if df is None:
            continue
        res = analyze_stock(df, info["ticker"], info["name"], "US",
                            benchmark_close, market_condition)
        count += 1
        if res:
            allow, flag = _get_market_filter_decision(market_condition, res["signal_type"])
            if not allow:
                logger.debug(f"[US] {info['ticker']} 필터됨: {flag}")
                continue
            if flag:
                res["_market_flag"] = flag
            _save(db, res)
            signals.append(res)

    return signals, count


def _check_watchlist(db, kr_bench=None, us_bench=None):
    """감시목록 매도 시그널 체크.

    Phase 2: 일봉(df) → 주봉(weekly_df)을 derive 해서 check_sell_signal에 전달.
    벤치마크가 주어지면 Mansfield RS 악화 분기까지 평가. 일봉/주봉/벤치마크
    가운데 어느 하나라도 미확보면 해당 분기는 None 폴백으로 graceful 처리.
    """
    from database.models import WatchList
    from scanner.weinstein import check_sell_signal, to_weekly_ohlcv
    from scanner.kr_stocks import get_kr_ohlcv
    from scanner.us_stocks import get_us_ohlcv

    items = db.query(WatchList).filter(WatchList.is_active == True).all()
    sells = []
    for w in items:
        try:
            df = get_kr_ohlcv(w.ticker) if w.market == "KR" else get_us_ohlcv(w.ticker)
            if df is None:
                continue
            weekly_df = to_weekly_ohlcv(df)
            if weekly_df is None or len(weekly_df) == 0:
                weekly_df = None
            bench = kr_bench if w.market == "KR" else us_bench
            sig = check_sell_signal(df, w.ticker, w.name, w.market,
                                    buy_price=w.buy_price, stop_loss=w.stop_loss,
                                    weekly_df=weekly_df, benchmark_close=bench)
            if sig:
                sells.append(sig)
        except Exception as e:
            logger.error(f"감시목록 체크 오류 {w.ticker}: {e}")
    return sells


def _save(db, signal: dict):
    from database.models import ScanResult
    try:
        existing = db.query(ScanResult).filter(
            ScanResult.ticker      == signal["ticker"],
            ScanResult.signal_date == signal.get("signal_date", ""),
            ScanResult.signal_type == signal["signal_type"],
        ).first()

        grade = _grade(signal)
        signal["_grade"] = grade  # notify에서 재사용

        if existing:
            # 최신 가격/품질만 업데이트
            existing.price            = signal["price"]
            existing.ma150            = signal["ma150"]
            existing.volume_ratio     = signal.get("volume_ratio", 0)
            existing.pivot_price      = signal.get("pivot_price")
            existing.support_level    = signal.get("support_level")
            existing.market_condition = signal.get("market_condition")
            existing.signal_quality   = signal.get("signal_quality")
            existing.rs_value         = signal.get("rs_value")
            existing.grade            = grade
            existing.scan_time        = datetime.utcnow()
        else:
            db.add(ScanResult(
                scan_time        = datetime.utcnow(),
                market           = signal["market"],
                ticker           = signal["ticker"],
                name             = signal["name"],
                signal_type      = signal["signal_type"],
                stage            = signal.get("stage", "STAGE2"),
                price            = signal["price"],
                ma150            = signal["ma150"],
                volume           = signal.get("volume", 0),
                volume_avg       = signal.get("volume_avg", 0),
                volume_ratio     = signal.get("volume_ratio", 0),
                signal_date      = signal.get("signal_date", ""),
                pivot_price      = signal.get("pivot_price"),
                support_level    = signal.get("support_level"),
                market_condition = signal.get("market_condition"),
                signal_quality   = signal.get("signal_quality"),
                rs_value         = signal.get("rs_value"),
                grade            = grade,
            ))
        db.commit()
    except Exception as e:
        logger.error(f"저장 오류: {e}")
        db.rollback()


def _sector_summary(market: str) -> str:
    """강세/약세 섹터 한 줄 요약 (실패 시 빈 문자열)."""
    try:
        from scanner.market_analysis import get_market_stages
        stages = get_market_stages()
        key    = "US_SECTORS" if market == "US" else "KR_SECTORS"
        etfs   = stages.get(key, [])
        if not etfs:
            return ""
        bull = [e["name"] for e in etfs if e["stage"] == "STAGE2"]
        bear = [e["name"] for e in etfs if e["stage"] == "STAGE4"]
        parts = []
        if bull: parts.append(f"강세: {', '.join(bull[:3])}")
        if bear: parts.append(f"약세: {', '.join(bear[:3])}")
        return "📊 " + " | ".join(parts) if parts else ""
    except Exception:
        return ""


def _notify(buys, sells, send_fn):
    if buys:
        kr  = [s for s in buys if s["market"] == "KR"]
        us  = [s for s in buys if s["market"] == "US"]
        msg = (f"📈 *Weinstein Stage2 매수 시그널*\n"
               f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')} KST\n\n")

        grade_icon   = {"S": "🔥", "A": "✅", "B": "📌"}
        signal_icon  = {"BREAKOUT": "🚀", "RE_BREAKOUT": "🔁", "REBOUND": "🔄"}

        for mkt_list, flag in ((kr, "🇰🇷"), (us, "🇺🇸")):
            if not mkt_list:
                continue
            mkt_name = "한국" if flag == "🇰🇷" else "미국"
            sector   = _sector_summary("KR" if flag == "🇰🇷" else "US")
            msg += f"{flag} *{mkt_name} 주식*"
            if sector:
                msg += f"\n{sector}"
            msg += "\n"

            for s in mkt_list[:10]:
                ico   = signal_icon.get(s["signal_type"], "🔹")
                g     = s.get("_grade", "B")
                gbadge = grade_icon.get(g, "📌")
                p     = (f"{s['price']:,.0f}원" if s["market"] == "KR"
                         else f"${s['price']:.2f}")
                flag_warn = f" _{s.get('_market_flag', '')}_" if s.get("_market_flag") else ""
                bq    = s.get("base_quality", "")
                bq_str = f" | 베이스 {bq}" if bq and bq not in ("N/A", "NONE") else ""
                msg += (f"{ico}{gbadge}[{g}] *{s['name']}* ({s['ticker']})\n"
                        f"  • {s['signal_type']} | {p} | 거래량 {s['volume_ratio']:.1f}x"
                        f"{bq_str}{flag_warn}\n"
                        f"  • 시그널일: {s['signal_date']}\n\n")
            if len(mkt_list) > 10:
                msg += f"  ... 외 {len(mkt_list) - 10}개\n\n"
        send_fn(msg)

    if sells:
        severity_icon = {"HIGH": "🔴", "MEDIUM": "🟠", "LOW": "🟡"}
        msg = (f"⚠️ *포트폴리오 매도 알림*\n"
               f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')} KST\n\n")
        for s in sells:
            pl  = f"{s['profit_pct']:+.1f}%" if s.get("profit_pct") is not None else "N/A"
            sev = severity_icon.get(s.get("severity", ""), "🔴")
            msg += (f"{sev} *{s['name']}* ({s['ticker']})\n"
                    f"  • {s['sell_reason']}\n"
                    f"  • 현재가: {s['price']:,.4g} | 수익률: {pl}\n\n")
        send_fn(msg)
