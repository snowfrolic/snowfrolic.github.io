"""주식·ETF 가격 데이터 수집 (yfinance)."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import yfinance as yf

from config import get_logger

log = get_logger(__name__)


@dataclass
class PriceSeries:
    ticker: str
    name: str
    daily: pd.DataFrame   # 일봉 (1y)
    weekly: pd.DataFrame  # 주봉 (3y)
    last_close: float
    prev_close: float
    pct_change: float

    @property
    def is_valid(self) -> bool:
        return not self.daily.empty and len(self.daily) >= 20


def fetch_history(ticker: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
    """yfinance에서 가격 시계열을 가져온다. 실패 시 빈 DataFrame."""
    try:
        df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False)
        if df.empty:
            log.warning(f"{ticker} 가격 데이터 비어 있음")
        return df
    except Exception as exc:
        log.error(f"{ticker} 가격 데이터 수집 실패: {exc}")
        return pd.DataFrame()


def fetch_price_series(ticker: str, name: str = "") -> PriceSeries | None:
    """일봉(1년) + 주봉(3년) 데이터를 한 번에 가져와 PriceSeries로 묶는다."""
    daily = fetch_history(ticker, period="1y", interval="1d")
    weekly = fetch_history(ticker, period="3y", interval="1wk")
    if daily.empty:
        return None

    closes = daily["Close"].dropna()
    if len(closes) < 2:
        return None

    last_close = float(closes.iloc[-1])
    prev_close = float(closes.iloc[-2])
    pct_change = (last_close - prev_close) / prev_close * 100

    return PriceSeries(
        ticker=ticker,
        name=name or ticker,
        daily=daily,
        weekly=weekly,
        last_close=last_close,
        prev_close=prev_close,
        pct_change=pct_change,
    )


def fetch_benchmarks(tickers: dict[str, str]) -> dict[str, PriceSeries]:
    """벤치마크 지수 묶음. {표시명: ticker} → {표시명: PriceSeries}."""
    out: dict[str, PriceSeries] = {}
    for name, ticker in tickers.items():
        series = fetch_price_series(ticker, name)
        if series:
            out[name] = series
    return out
