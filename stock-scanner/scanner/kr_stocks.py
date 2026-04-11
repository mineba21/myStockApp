"""한국 주식 데이터 (KOSPI + KOSDAQ) - pykrx + FinanceDataReader"""
import pandas as pd
import time
import logging
from typing import Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def get_all_kr_tickers() -> list:
    try:
        from pykrx import stock as px
        today = datetime.now().strftime("%Y%m%d")
        tickers = []
        for mkt in ("KOSPI", "KOSDAQ"):
            for t in px.get_market_ticker_list(today, market=mkt):
                name = px.get_market_ticker_name(t)
                tickers.append({"ticker": t, "name": name, "market_type": mkt})
        logger.info(f"한국 전종목: {len(tickers)}개")
        return tickers
    except Exception as e:
        logger.error(f"한국 티커 조회 실패: {e}")
        return []


def get_kr_ohlcv(ticker: str, period_years: int = 2) -> Optional[pd.DataFrame]:
    end   = datetime.now()
    start = end - timedelta(days=period_years * 365)

    # FinanceDataReader 먼저 시도
    try:
        import FinanceDataReader as fdr
        df = fdr.DataReader(ticker, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        if df is not None and len(df) > 50:
            df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
            df.index = pd.to_datetime(df.index)
            return df
    except Exception:
        pass

    # pykrx fallback
    try:
        from pykrx import stock as px
        df = px.get_market_ohlcv(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker)
        if df is not None and len(df) > 50:
            df = df.rename(columns={"시가": "Open", "고가": "High", "저가": "Low",
                                    "종가": "Close", "거래량": "Volume"})
            df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
            df.index = pd.to_datetime(df.index)
            return df
    except Exception as e:
        logger.debug(f"KR {ticker} 조회 실패: {e}")

    return None


def get_kr_batch(tickers: list, progress_callback=None, delay: float = 0.05) -> list:
    results = []
    for i, info in enumerate(tickers):
        df = get_kr_ohlcv(info["ticker"])
        results.append((info, df))
        if progress_callback:
            progress_callback(i + 1, len(tickers), f"KR [{i+1}/{len(tickers)}] {info['name']}")
        time.sleep(delay)
    return results
