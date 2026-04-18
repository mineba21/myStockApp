"""스캔 엔진 - 전체 스캔 오케스트레이션"""
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

scan_status = {
    "is_running": False, "market": "",
    "progress": 0, "total": 0,
    "current_stock": "", "started_at": None,
}


def _prog(cur, tot, msg=""):
    scan_status.update(progress=cur, total=tot, current_stock=msg)


def run_scan(market: str = "ALL", triggered_by: str = "manual") -> dict:
    if scan_status["is_running"]:
        return {"status": "already_running"}

    scan_status.update(is_running=True, market=market,
                       progress=0, total=0, started_at=datetime.now().isoformat())

    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from database.models import SessionLocal, ScanResult, ScanLog, WatchList
    from scanner.weinstein import analyze_stock, check_sell_signal
    from notifications.telegram import send_telegram_message
    from config import US_UNIVERSE

    db  = SessionLocal()
    log = ScanLog(market=market, triggered_by=triggered_by, status="RUNNING")
    db.add(log); db.commit(); db.refresh(log)

    buy_signals, total_scanned = [], 0

    try:
        # 벤치마크 사전 로드 (RS 계산용)
        from scanner.market_analysis import get_benchmark_close
        kr_bench = get_benchmark_close("KR") if market in ("KR", "ALL") else None
        us_bench = get_benchmark_close("US") if market in ("US", "ALL") else None

        if market in ("KR", "ALL"):
            sigs, cnt = _scan_kr(db, kr_bench)
            buy_signals.extend(sigs); total_scanned += cnt

        if market in ("US", "ALL"):
            sigs, cnt = _scan_us(db, US_UNIVERSE, us_bench)
            buy_signals.extend(sigs); total_scanned += cnt

        sell_signals = _check_watchlist(db)

        if buy_signals or sell_signals:
            _notify(buy_signals, sell_signals, send_telegram_message)

        log.finished_at     = datetime.utcnow()
        log.total_scanned   = total_scanned
        log.signals_found   = len(buy_signals)
        log.status          = "DONE"
        db.commit()

        return {"status": "done", "total_scanned": total_scanned,
                "signals_found": len(buy_signals), "sell_signals": len(sell_signals)}

    except Exception as e:
        logger.error(f"스캔 오류: {e}", exc_info=True)
        log.status = "ERROR"; log.error_msg = str(e)
        log.finished_at = datetime.utcnow(); db.commit()
        return {"status": "error", "message": str(e)}

    finally:
        scan_status["is_running"] = False
        db.close()


def _scan_kr(db, benchmark_close=None):
    from scanner.kr_stocks import get_all_kr_tickers, get_kr_ohlcv
    from scanner.weinstein import analyze_stock
    from database.models import ScanResult

    tickers  = get_all_kr_tickers()
    signals, count = [], 0
    for i, info in enumerate(tickers):
        _prog(i + 1, len(tickers), f"KR [{i+1}/{len(tickers)}] {info['name']}")
        df = get_kr_ohlcv(info["ticker"])
        if df is None: continue
        res = analyze_stock(df, info["ticker"], info["name"], "KR", benchmark_close)
        count += 1
        if res:
            _save(db, res)
            signals.append(res)
            logger.info(f"[KR] {info['ticker']} {info['name']}: {res['signal_type']}")
        import time; time.sleep(0.05)

    return signals, count


def _scan_us(db, universe, benchmark_close=None):
    from scanner.us_stocks import get_all_us_tickers, get_us_batch
    from scanner.weinstein import analyze_stock

    tickers = get_all_us_tickers(universe)
    results = get_us_batch(tickers, progress_callback=_prog)
    signals, count = [], 0
    for info, df in results:
        if df is None: continue
        res = analyze_stock(df, info["ticker"], info["name"], "US", benchmark_close)
        count += 1
        if res:
            _save(db, res)
            signals.append(res)
    return signals, count


def _check_watchlist(db):
    from database.models import WatchList
    from scanner.weinstein import check_sell_signal
    from scanner.kr_stocks import get_kr_ohlcv
    from scanner.us_stocks import get_us_ohlcv

    items = db.query(WatchList).filter(WatchList.is_active == True).all()
    sells = []
    for w in items:
        try:
            df = get_kr_ohlcv(w.ticker) if w.market == "KR" else get_us_ohlcv(w.ticker)
            if df is None: continue
            sig = check_sell_signal(df, w.ticker, w.name, w.market,
                                    buy_price=w.buy_price, stop_loss=w.stop_loss)
            if sig: sells.append(sig)
        except Exception as e:
            logger.error(f"감시목록 체크 오류 {w.ticker}: {e}")
    return sells


def _grade(signal: dict) -> str:
    """
    Signal quality grade.
    S: BREAKOUT + base_quality=STRONG + volume_ratio >= 2.0
    A: BREAKOUT + base_quality=WEAK or volume_ratio >= 1.5
    B: RE_BREAKOUT or REBOUND
    """
    try:
        st = signal.get("signal_type")
        v = float(signal.get("volume_ratio", 0) or 0)
        bq = signal.get("base_quality", "NONE")
        if st == "BREAKOUT" and bq == "STRONG" and v >= 2.0:
            return "S"
        if st == "BREAKOUT" and (bq == "WEAK" or v >= 1.5):
            return "A"
        if st in ("RE_BREAKOUT", "REBOUND"):
            return "B"
    except Exception:
        pass
    return "B"


def _save(db, signal: dict):
    from database.models import ScanResult
    try:
        grade = _grade(signal)
        # 같은 ticker + signal_date + signal_type 중복 저장 방지
        existing = db.query(ScanResult).filter(
            ScanResult.ticker == signal["ticker"],
            ScanResult.signal_date == signal.get("signal_date", ""),
            ScanResult.signal_type == signal["signal_type"],
        ).first()
        if existing:
            # 가격 정보만 업데이트
            existing.price = signal["price"]
            existing.ma150 = signal["ma150"]
            existing.volume_ratio = signal.get("volume_ratio", 0)
            existing.grade = grade
            existing.scan_time = datetime.utcnow()
        else:
            db.add(ScanResult(
                scan_time=datetime.utcnow(),
                market=signal["market"], ticker=signal["ticker"], name=signal["name"],
                signal_type=signal["signal_type"], stage=signal.get("stage", "STAGE2"),
                price=signal["price"], ma150=signal["ma150"],
                volume=signal.get("volume", 0), volume_avg=signal.get("volume_avg", 0),
                volume_ratio=signal.get("volume_ratio", 0),
                grade=grade,
                signal_date=signal.get("signal_date", ""),
            ))
        db.commit()
    except Exception as e:
        logger.error(f"저장 오류: {e}"); db.rollback()


def _notify(buys, sells, send_fn):
    if buys:
        from scanner.market_analysis import get_market_stages

        def _badge(g: str) -> str:
            return {"S": "🔥", "A": "✅", "B": "📌"}.get(g, "📌")

        def _sector_line(sectors: list) -> str:
            try:
                strong = [f"{s['name']}(S2)" for s in sectors if s.get("stage") == "STAGE2"]
                weak = [f"{s['name']}(S4)" for s in sectors if s.get("stage") == "STAGE4"]
                if not strong and not weak:
                    return ""
                left = " ".join(strong[:3]) if strong else "-"
                right = " ".join(weak[:3]) if weak else "-"
                return f"📊 강세 섹터: {left} | 약세: {right}\n"
            except Exception:
                return ""

        sectors = {}
        try:
            sectors = get_market_stages()
        except Exception as e:
            logger.debug(f"시장 섹터 조회 실패: {e}")

        kr = [s for s in buys if s["market"] == "KR"]
        us = [s for s in buys if s["market"] == "US"]
        msg = f"📈 *Weinstein Stage2 매수 시그널*\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')} KST\n\n"
        for mkt_list, flag in ((kr, "🇰🇷"), (us, "🇺🇸")):
            if not mkt_list: continue
            msg += f"{flag} *{'한국' if flag=='🇰🇷' else '미국'} 주식*\n"
            if flag == "🇰🇷":
                msg += _sector_line(sectors.get("KR_SECTORS", []))
            else:
                msg += _sector_line(sectors.get("US_SECTORS", []))
            for s in mkt_list[:10]:
                ico = "🚀" if s["signal_type"] == "BREAKOUT" else "🔄"
                grade = _grade(s)
                p   = f"{s['price']:,.0f}원" if s["market"] == "KR" else f"${s['price']:.2f}"
                msg += (
                    f"{_badge(grade)} {ico} *{s['name']}* ({s['ticker']})\n"
                    f"  • {s['signal_type']} | 등급 {grade} | {p} | 거래량 {s['volume_ratio']:.1f}x\n"
                    f"  • 시그널일: {s['signal_date']}\n\n"
                )
            if len(mkt_list) > 10:
                msg += f"  ... 외 {len(mkt_list)-10}개\n\n"
        send_fn(msg)

    if sells:
        msg = f"⚠️ *포트폴리오 매도 알림*\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')} KST\n\n"
        for s in sells:
            pl  = f"{s['profit_pct']:+.1f}%" if s.get("profit_pct") is not None else "N/A"
            msg += f"🔴 *{s['name']}* ({s['ticker']})\n  • {s['sell_reason']}\n  • 현재가: {s['price']:,.0f} | 수익률: {pl}\n\n"
        send_fn(msg)
