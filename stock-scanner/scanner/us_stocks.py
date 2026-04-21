"""미국 주식 데이터 - yfinance / FinanceDataReader"""
import io
import pandas as pd
import yfinance as yf
import requests
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)
_cache: dict = {}   # universe_key → list

# SP500/NASDAQ100 중복 등록되는 대표 심볼 제외 (BRK-B=BRK-A 등)
EXCLUDE_US: set = {"GOOGL"}   # GOOG 와 중복; 필요 시 추가

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _read_html_wiki(url: str) -> list:
    """Wikipedia 403 우회: requests로 HTML 받아서 pd.read_html에 전달"""
    resp = requests.get(url, headers=_HEADERS, timeout=20)
    resp.raise_for_status()
    return pd.read_html(io.StringIO(resp.text))


def get_sp500_tickers() -> list:
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        tables = _read_html_wiki(url)
        df = tables[0]
        return [{"ticker": str(r["Symbol"]).replace(".", "-"),
                 "name": str(r["Security"]), "market_type": "SP500"}
                for _, r in df.iterrows()]
    except Exception as e:
        logger.error(f"S&P500 목록 실패: {e}"); return []


def get_nasdaq100_tickers() -> list:
    try:
        url    = "https://en.wikipedia.org/wiki/Nasdaq-100"
        tables = _read_html_wiki(url)
        for t in tables:
            # 멀티레벨 헤더 평탄화
            if isinstance(t.columns, pd.MultiIndex):
                t.columns = [" ".join(str(c) for c in col).strip() for col in t.columns]
            cols = [str(c) for c in t.columns]
            tcol = next((c for c in cols if "ticker" in c.lower() or "symbol" in c.lower()), None)
            ncol = next((c for c in cols if "company" in c.lower() or "name" in c.lower()), None)
            if tcol:
                return [{"ticker": str(r[tcol]).replace(".", "-"),
                         "name":   str(r[ncol]) if ncol else str(r[tcol]),
                         "market_type": "NASDAQ100"}
                        for _, r in t.iterrows()]
    except Exception as e:
        logger.error(f"NASDAQ100 목록 실패: {e}")
    return []


def get_nyse_tickers() -> list:
    """NYSE 전체 상장 종목 (FinanceDataReader)"""
    try:
        import FinanceDataReader as fdr
        df = fdr.StockListing('NYSE')
        return [{"ticker": str(r["Symbol"]).replace(".", "-"),
                 "name": str(r["Name"]), "market_type": "NYSE"}
                for _, r in df.iterrows()
                if str(r["Symbol"]).isalpha() and len(str(r["Symbol"])) <= 5]
    except Exception as e:
        logger.error(f"NYSE 목록 실패: {e}"); return []


def get_nasdaq_tickers() -> list:
    """NASDAQ 전체 상장 종목 (FinanceDataReader)"""
    try:
        import FinanceDataReader as fdr
        df = fdr.StockListing('NASDAQ')
        return [{"ticker": str(r["Symbol"]).replace(".", "-"),
                 "name": str(r["Name"]), "market_type": "NASDAQ"}
                for _, r in df.iterrows()
                if str(r["Symbol"]).isalpha() and len(str(r["Symbol"])) <= 5]
    except Exception as e:
        logger.error(f"NASDAQ 목록 실패: {e}"); return []


def get_all_us_tickers(universe: str = "sp500+nasdaq100") -> list:
    global _cache
    key = universe.lower().strip()
    if key in _cache:
        return _cache[key]

    seen, tickers = set(), []

    def _add(items):
        for t in items:
            if t["ticker"] not in seen and t["ticker"] not in EXCLUDE_US:
                tickers.append(t)
                seen.add(t["ticker"])

    use_all = key in ("all", "")

    # S&P500
    if use_all or "sp500" in key:
        _add(get_sp500_tickers())

    # NASDAQ100
    if use_all or "nasdaq100" in key:
        _add(get_nasdaq100_tickers())

    # NYSE 전체 (sp500/nasdaq100과 중복 제거됨)
    if use_all or "nyse" in key:
        _add(get_nyse_tickers())

    # NASDAQ 전체 (sp500/nasdaq100과 중복 제거됨)
    if use_all or "nasdaq" in key:
        _add(get_nasdaq_tickers())

    logger.info(f"US 유니버스 [{universe}]: {len(tickers)}개")
    _cache[key] = tickers
    return tickers


def get_us_ohlcv(ticker: str, period: str = "2y") -> Optional[pd.DataFrame]:
    try:
        df = yf.Ticker(ticker).history(period=period, auto_adjust=True)
        if df is None or len(df) < 50: return None
        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return df
    except Exception as e:
        logger.debug(f"US {ticker} 실패: {e}"); return None


def get_us_batch(tickers: list, progress_callback=None, delay: float = 0.1) -> list:
    results, total, bs = [], len(tickers), 50
    for start in range(0, total, bs):
        batch = tickers[start:start + bs]
        syms  = [t["ticker"] for t in batch]
        try:
            raw = yf.download(syms, period="2y", auto_adjust=True,
                              group_by="ticker", threads=True, progress=False)
            for info in batch:
                sym = info["ticker"]
                try:
                    df = (raw[["Open","High","Low","Close","Volume"]] if len(syms)==1
                          else raw[sym][["Open","High","Low","Close","Volume"]]).dropna()
                    df.index = pd.to_datetime(df.index).tz_localize(None)
                    results.append((info, df if len(df) >= 50 else None))
                except Exception:
                    results.append((info, None))
        except Exception as e:
            logger.error(f"US 배치 실패: {e}")
            for info in batch: results.append((info, None))

        if progress_callback:
            progress_callback(min(start + bs, total), total,
                              f"US [{min(start+bs,total)}/{total}]")
        time.sleep(delay)
    return results
