"""한국 주식 데이터 (KOSPI + KOSDAQ) - pykrx + FinanceDataReader"""
import pandas as pd
import time
import logging
from typing import Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

MIN_MARKET_CAP = 100_000_000_000
MIN_PRICE = 1_000
EXCLUDE_KEYWORDS = ['스팩', 'SPAC', '리츠', 'ETF', 'ETN', '선물', '인버스', '레버리지']


def get_all_kr_tickers() -> list:
    try:
        from pykrx import stock as px
        today = datetime.now().strftime("%Y%m%d")
        tickers = []
        for mkt in ("KOSPI", "KOSDAQ"):
            for t in px.get_market_ticker_list(today, market=mkt):
                name = px.get_market_ticker_name(t)
                tickers.append({"ticker": t, "name": name, "market_type": mkt})

        try:
            # Apply lightweight large-cap and instrument filters.
            cap_df = px.get_market_cap_by_ticker(today)
            if cap_df is None or cap_df.empty:
                raise ValueError("empty market cap dataframe")

            filtered = []
            for info in tickers:
                t = info["ticker"]
                name = str(info.get("name", ""))
                cap = cap_df.loc[t, "시가총액"] if t in cap_df.index else 0
                price = cap_df.loc[t, "종가"] if t in cap_df.index else 0
                if cap < MIN_MARKET_CAP:
                    continue
                if price < MIN_PRICE:
                    continue
                upper_name = name.upper()
                if any(k.upper() in upper_name for k in EXCLUDE_KEYWORDS):
                    continue
                filtered.append(info)
            logger.info(f"한국 종목 필터링: {len(tickers)}개 -> {len(filtered)}개")
            return filtered
        except Exception as e:
            logger.warning(f"한국 종목 필터 비활성화(원본 반환): {e}")

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
