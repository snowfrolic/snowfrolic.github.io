"""주식·ETF 가격 데이터 수집.

한국 종목 (.KS / .KQ / ^KS* / ^KQ*) → FinanceDataReader 우선 (네이버 금융 데이터, 지연 적음)
미국·기타                          → yfinance
FDR 실패 시 yfinance 폴백.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

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


# ──────────────────────────────────────────────────────────────────
# FinanceDataReader 우회 — 한국 종목용
# ──────────────────────────────────────────────────────────────────

def _to_fdr_ticker(ticker: str) -> str | None:
    """yfinance 티커 → FDR 티커. 한국 종목만 변환, 외 None."""
    if ticker == "^KS11":
        return "KS11"
    if ticker == "^KQ11":
        return "KQ11"
    if ticker.endswith(".KS"):
        return ticker[:-3]
    if ticker.endswith(".KQ"):
        return ticker[:-3]
    return None


def _fdr_history(fdr_ticker: str, days: int) -> pd.DataFrame:
    """FDR로 일봉. yfinance 형식(Open/High/Low/Close/Volume)으로 정규화."""
    try:
        import FinanceDataReader as fdr
    except ImportError:
        log.warning("FinanceDataReader 미설치 — pip install finance-datareader")
        return pd.DataFrame()

    end = datetime.now().date()
    start = end - timedelta(days=days)
    try:
        df = fdr.DataReader(fdr_ticker, start, end)
    except Exception as exc:
        log.debug(f"FDR {fdr_ticker} 조회 실패: {exc}")
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    # 컬럼 정규화 — 지수는 Open/High/Low가 없을 수 있어 Close로 복사
    norm = pd.DataFrame(index=df.index)
    norm["Close"] = df["Close"] if "Close" in df.columns else pd.NA
    norm["Open"]  = df["Open"]  if "Open"  in df.columns else norm["Close"]
    norm["High"]  = df["High"]  if "High"  in df.columns else norm["Close"]
    norm["Low"]   = df["Low"]   if "Low"   in df.columns else norm["Close"]
    norm["Volume"] = df["Volume"] if "Volume" in df.columns else 0
    norm = norm.dropna(subset=["Close"])
    return norm


def _resample_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """일봉 → 주봉. W-FRI 마감 기준 OHLCV 집계."""
    if daily.empty:
        return daily
    return daily.resample("W-FRI").agg({
        "Open":   "first",
        "High":   "max",
        "Low":    "min",
        "Close":  "last",
        "Volume": "sum",
    }).dropna(subset=["Close"])


# ──────────────────────────────────────────────────────────────────
# 기존 yfinance 경로
# ──────────────────────────────────────────────────────────────────

def _yf_history(ticker: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
    try:
        df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False)
        if df.empty:
            log.warning(f"{ticker} 가격 데이터 비어 있음 (yfinance)")
        return df
    except Exception as exc:
        log.error(f"{ticker} 가격 데이터 수집 실패 (yfinance): {exc}")
        return pd.DataFrame()


# 외부 노출 fetch_history — 기존 API 호환 유지 (필요 시 사용)
def fetch_history(ticker: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
    return _yf_history(ticker, period, interval)


# ──────────────────────────────────────────────────────────────────
# 통합 가격 시계열 수집 — FDR 우선, yfinance 폴백
# ──────────────────────────────────────────────────────────────────

def fetch_price_series(ticker: str, name: str = "") -> PriceSeries | None:
    """일봉(1년) + 주봉(3년) 데이터. 한국 종목은 FDR 우선."""
    daily = pd.DataFrame()
    weekly = pd.DataFrame()
    source = "yfinance"

    fdr_ticker = _to_fdr_ticker(ticker)
    if fdr_ticker:
        # FDR로 일봉 380일 (1y 이상 여유), 주봉은 일봉에서 resampling
        daily_fdr = _fdr_history(fdr_ticker, days=400)
        if not daily_fdr.empty and len(daily_fdr) >= 20:
            daily = daily_fdr
            # 주봉은 3년치 일봉이 필요하므로 별도 조회
            daily_3y = _fdr_history(fdr_ticker, days=3 * 380)
            weekly = _resample_weekly(daily_3y) if not daily_3y.empty else _resample_weekly(daily_fdr)
            source = "FDR"

    if daily.empty:
        # yfinance 폴백
        daily = _yf_history(ticker, period="1y", interval="1d")
        weekly = _yf_history(ticker, period="3y", interval="1wk")

    if daily.empty:
        return None

    closes = daily["Close"].dropna()
    if len(closes) < 2:
        return None

    last_close = float(closes.iloc[-1])
    prev_close = float(closes.iloc[-2])
    pct_change = (last_close - prev_close) / prev_close * 100

    if source == "FDR":
        log.debug(f"{ticker} ({fdr_ticker}) FDR 사용 — 최근 {closes.index[-1].date()}")

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
    out: dict[str, PriceSeries] = {}
    for name, ticker in tickers.items():
        series = fetch_price_series(ticker, name)
        if series:
            out[name] = series
    return out
