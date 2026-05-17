"""기술적 지표 계산 — 외부 ta-lib 없이 pandas/numpy로 직접 구현."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class TechSnapshot:
    """단일 종목의 기술적 지표 스냅샷."""
    close: float
    ma20: float | None
    ma60: float | None
    ma120: float | None
    ma200: float | None
    ma_weekly_20: float | None  # 주봉 20주 = 약 5개월
    rsi14: float | None
    macd: float | None
    macd_signal: float | None
    macd_hist: float | None
    bb_upper: float | None
    bb_lower: float | None
    bb_pct: float | None  # 가격이 밴드 내 위치 (0=하단, 1=상단)
    volume_ratio: float | None  # 최근 거래량 / 20일 평균
    disparity_20: float | None  # 이격도 (close / ma20 * 100)
    chg_1d: float | None
    chg_5d: float | None
    chg_20d: float | None


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


def compute_tech(daily: pd.DataFrame, weekly: pd.DataFrame) -> TechSnapshot:
    """일봉·주봉 데이터로부터 모든 기술 지표 계산."""
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
        chg_1d=pct(1),
        chg_5d=pct(5),
        chg_20d=pct(20),
    )
