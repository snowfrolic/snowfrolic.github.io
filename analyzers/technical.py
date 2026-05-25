"""기술적 지표 계산 — 외부 ta-lib 없이 pandas/numpy로 직접 구현.

v2: 수급 구조 지표 추가 — 상대강도(RS), 거래량 돌파, 유동성(거래대금), 신고가 돌파.
    기관식 접근: 복잡한 보조지표보다 실제 돈의 흐름(거래량·유동성·상대강도)을 우선.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class TechSnapshot:
    """단일 종목의 기술적 지표 + 수급 구조 스냅샷."""
    # ── 기존 보조지표 ──
    close: float
    ma20: float | None
    ma60: float | None
    ma120: float | None
    ma200: float | None
    ma_weekly_20: float | None
    rsi14: float | None
    macd: float | None
    macd_signal: float | None
    macd_hist: float | None
    bb_upper: float | None
    bb_lower: float | None
    bb_pct: float | None
    volume_ratio: float | None       # 당일 거래량 / 20일 평균
    disparity_20: float | None
    chg_1d: float | None
    chg_5d: float | None
    chg_20d: float | None

    # ── 수급 구조 지표 (v2 신규) ──
    rs_vs_market_20d: float | None    # 시장 대비 20일 상대강도 (%p)
    rs_vs_market_60d: float | None    # 60일 상대강도
    volume_breakout: str | None       # "bullish" / "bearish" / None
    avg_turnover_20d: float | None    # 20일 평균 거래대금 (원)
    turnover_trend: float | None      # 최근 5일 거래대금 / 20일 평균 (>1 = 확대)
    near_52w_high: bool = False       # 52주 범위 95%+ 접근
    hi52w_breakout_with_vol: bool = False  # 52주 접근 + 거래량 1.5배 이상
    # 숏 인터레스트 (미국 종목만, yfinance info)
    short_ratio: float | None = None       # days to cover
    short_pct_float: float | None = None   # 유통주식 대비 공매도 비율 (%)
    # 섹터 상대강도
    rs_vs_sector_20d: float | None = None  # 섹터 ETF 대비 20일 RS (%p)


# ──────────────────────────────────────────────────────────────────

def _sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(window=n, min_periods=n).mean()


def _ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / n, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    macd = ema12 - ema26
    signal = _ema(macd, 9)
    hist = macd - signal
    return macd, signal, hist


def _bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    ma = _sma(close, n)
    std = close.rolling(window=n, min_periods=n).std()
    upper = ma + k * std
    lower = ma - k * std
    return upper, lower


def _last(s: pd.Series) -> float | None:
    s = s.dropna()
    return float(s.iloc[-1]) if not s.empty else None


def _relative_strength(stock_close: pd.Series, market_close: pd.Series, n: int) -> float | None:
    """종목의 N일 수익률 - 시장의 N일 수익률 (%p)."""
    if len(stock_close) <= n or len(market_close) <= n:
        return None
    stock_ret = (float(stock_close.iloc[-1]) / float(stock_close.iloc[-1 - n]) - 1) * 100
    market_ret = (float(market_close.iloc[-1]) / float(market_close.iloc[-1 - n]) - 1) * 100
    return round(stock_ret - market_ret, 2)


def compute_tech(
    daily: pd.DataFrame,
    weekly: pd.DataFrame,
    market_daily_close: pd.Series | None = None,
) -> TechSnapshot:
    """일봉·주봉 데이터로부터 기술 지표 + 수급 구조 지표 계산.

    market_daily_close: 벤치마크(KOSPI/S&P500)의 일봉 Close. 상대강도 계산용.
    """
    close = daily["Close"].dropna()
    volume = daily["Volume"].dropna() if "Volume" in daily else pd.Series(dtype=float)

    ma20 = _sma(close, 20)
    ma60 = _sma(close, 60)
    ma120 = _sma(close, 120)
    ma200 = _sma(close, 200)

    rsi = _rsi(close, 14)
    macd_line, macd_sig, macd_hist = _macd(close)
    bb_up, bb_lo = _bollinger(close, 20, 2.0)

    last_close = float(close.iloc[-1])
    last_up = _last(bb_up)
    last_lo = _last(bb_lo)
    bb_pct = None
    if last_up is not None and last_lo is not None and last_up != last_lo:
        bb_pct = (last_close - last_lo) / (last_up - last_lo)

    # ── 거래량 기본 ──
    volume_ratio = None
    if not volume.empty and len(volume) >= 21:
        avg_vol = volume.tail(21).head(20).mean()
        if avg_vol > 0:
            volume_ratio = float(volume.iloc[-1] / avg_vol)

    last_ma20 = _last(ma20)
    disparity = (last_close / last_ma20 * 100) if last_ma20 else None

    def pct(n: int) -> float | None:
        if len(close) <= n:
            return None
        return float((close.iloc[-1] / close.iloc[-1 - n] - 1) * 100)

    weekly_ma20 = None
    if not weekly.empty:
        wc = weekly["Close"].dropna()
        weekly_ma20 = _last(_sma(wc, 20))

    # ── 수급 구조: 상대강도 ──
    rs_20 = None
    rs_60 = None
    if market_daily_close is not None:
        mkt = market_daily_close.dropna()
        rs_20 = _relative_strength(close, mkt, 20)
        rs_60 = _relative_strength(close, mkt, 60)

    # ── 수급 구조: 거래량 돌파 ──
    volume_breakout = None
    chg_1d = pct(1)
    if volume_ratio is not None and volume_ratio >= 2.0 and chg_1d is not None:
        if chg_1d >= 2.0:
            volume_breakout = "bullish"
        elif chg_1d <= -2.0:
            volume_breakout = "bearish"

    # ── 수급 구조: 유동성 (거래대금) ──
    avg_turnover_20d = None
    turnover_trend = None
    if not volume.empty and not close.empty and len(volume) >= 21:
        turnover = close * volume  # 일별 거래대금
        avg_20 = turnover.tail(21).head(20).mean()
        avg_5 = turnover.tail(5).mean()
        if avg_20 > 0:
            avg_turnover_20d = float(avg_20)
            turnover_trend = float(avg_5 / avg_20)

    # ── 수급 구조: 52주 신고가 접근 ──
    near_52w = False
    hi52w_breakout = False
    if len(close) >= 252:
        high_52w = float(close.tail(252).max())
        low_52w = float(close.tail(252).min())
        if high_52w > low_52w:
            pct_of_range = (last_close - low_52w) / (high_52w - low_52w) * 100
            near_52w = pct_of_range >= 95
            if near_52w and volume_ratio is not None and volume_ratio >= 1.5:
                hi52w_breakout = True

    return TechSnapshot(
        close=last_close,
        ma20=_last(ma20),
        ma60=_last(ma60),
        ma120=_last(ma120),
        ma200=_last(ma200),
        ma_weekly_20=weekly_ma20,
        rsi14=_last(rsi),
        macd=_last(macd_line),
        macd_signal=_last(macd_sig),
        macd_hist=_last(macd_hist),
        bb_upper=last_up,
        bb_lower=last_lo,
        bb_pct=bb_pct,
        volume_ratio=volume_ratio,
        disparity_20=disparity,
        chg_1d=chg_1d,
        chg_5d=pct(5),
        chg_20d=pct(20),
        # v2 수급 구조
        rs_vs_market_20d=rs_20,
        rs_vs_market_60d=rs_60,
        volume_breakout=volume_breakout,
        avg_turnover_20d=avg_turnover_20d,
        turnover_trend=turnover_trend,
        near_52w_high=near_52w,
        hi52w_breakout_with_vol=hi52w_breakout,
    )
