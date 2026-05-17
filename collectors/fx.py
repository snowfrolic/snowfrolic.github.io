"""환율 · 변동성 데이터 수집."""
from __future__ import annotations

from collectors.prices import fetch_price_series, PriceSeries
from config import FX_TICKERS, get_logger

log = get_logger(__name__)


def fetch_fx() -> dict[str, PriceSeries]:
    out: dict[str, PriceSeries] = {}
    for name, ticker in FX_TICKERS.items():
        series = fetch_price_series(ticker, name)
        if series:
            out[name] = series
    return out
