"""거시지표 수집 — 금리·수익률곡선·원자재.

MVP는 yfinance만 사용해 미국채 10Y/3M, VIX, 원자재를 가져옵니다.
FRED_API_KEY가 있으면 fed funds rate, 미국 CPI, 실업률, 비농업 등으로 보강.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import requests

from collectors.prices import fetch_price_series, PriceSeries
from config import FRED_API_KEY, MACRO_TICKERS, get_logger

log = get_logger(__name__)


@dataclass
class YieldCurve:
    us_10y: float | None
    us_3m: float | None
    us_30y: float | None
    spread_10y_3m: float | None
    inverted: bool
    history_30d_spread: list[float]


@dataclass
class MacroSnapshot:
    indicators: dict[str, PriceSeries]
    yield_curve: YieldCurve
    fred: dict[str, float]  # {지표명: 최신값}


def fetch_yield_curve() -> YieldCurve:
    """미국채 10Y·3M·30Y를 yfinance에서 가져와 스프레드 계산."""
    tnx = fetch_price_series("^TNX", "미국채10Y")  # 10년물
    irx = fetch_price_series("^IRX", "미국채3M")   # 13주 (3M 대용)
    tyx = fetch_price_series("^TYX", "미국채30Y")

    us_10y = tnx.last_close if tnx else None
    us_3m = irx.last_close if irx else None
    us_30y = tyx.last_close if tyx else None
    spread = None
    inverted = False
    history = []

    if us_10y is not None and us_3m is not None:
        spread = us_10y - us_3m
        inverted = spread < 0
        if tnx and irx:
            n = min(30, len(tnx.daily), len(irx.daily))
            t = tnx.daily["Close"].tail(n).reset_index(drop=True)
            i = irx.daily["Close"].tail(n).reset_index(drop=True)
            history = (t - i).tolist()

    return YieldCurve(
        us_10y=us_10y,
        us_3m=us_3m,
        us_30y=us_30y,
        spread_10y_3m=spread,
        inverted=inverted,
        history_30d_spread=history,
    )


FRED_SERIES = {
    "FEDFUNDS": "미국 기준금리(%)",
    "CPIAUCSL": "미국 CPI",
    "UNRATE": "미국 실업률(%)",
    "PAYEMS": "비농업 고용자수(천명)",
    "T10Y2Y": "미국 10Y-2Y 스프레드(%)",
    "DGS2": "미국채 2Y(%)",
    "BAMLH0A0HYM2": "미국 하이일드 스프레드(%)",
}


def fetch_fred() -> dict[str, float]:
    """FRED API에서 최신 거시지표 가져오기. 키 없으면 빈 dict."""
    if not FRED_API_KEY:
        log.info("FRED_API_KEY 미설정 — 거시지표 일부 생략")
        return {}

    out: dict[str, float] = {}
    base = "https://api.stlouisfed.org/fred/series/observations"
    for code, label in FRED_SERIES.items():
        try:
            r = requests.get(
                base,
                params={
                    "series_id": code,
                    "api_key": FRED_API_KEY,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": 2,
                },
                timeout=15,
            )
            r.raise_for_status()
            obs = r.json().get("observations", [])
            for row in obs:
                val = row.get("value")
                if val and val != ".":
                    out[label] = float(val)
                    break
        except Exception as exc:
            log.warning(f"FRED {code} 수집 실패: {exc}")
    return out


def fetch_macro_snapshot() -> MacroSnapshot:
    """매크로 전체 스냅샷."""
    indicators: dict[str, PriceSeries] = {}
    for name, ticker in MACRO_TICKERS.items():
        series = fetch_price_series(ticker, name)
        if series:
            indicators[name] = series

    return MacroSnapshot(
        indicators=indicators,
        yield_curve=fetch_yield_curve(),
        fred=fetch_fred(),
    )
