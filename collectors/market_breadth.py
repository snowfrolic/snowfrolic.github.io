"""시장 폭(Market Breadth) 지표.

전체 종목 NH/NL은 무거우니 대용 지표로 측정:
- **US Breadth**: RSP (S&P500 동일가중) / SPY (시총가중) 비율 추세
  비율 상승 = 시장 폭 확장 (소형주도 같이 오름, 건강함)
  비율 하락 = 대형주 쏠림 (시장 폭 축소, 후기 강세 위험 신호)
- **KR Breadth**: KOSDAQ / KOSPI 비율 (소형/중형 추세)
- **포트 Breadth**: 보유 종목 중 52주 신고가 접근 비율
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from config import get_logger

log = get_logger(__name__)


@dataclass
class MarketBreadth:
    us_breadth_ratio: float | None = None       # RSP/SPY 또는 SPY/RSP의 최근값
    us_breadth_chg_20d: float | None = None     # 20일 변화 (%)
    us_interpretation: str = ""
    kr_breadth_ratio: float | None = None       # KOSDAQ/KOSPI
    kr_breadth_chg_20d: float | None = None
    kr_interpretation: str = ""
    portfolio_breadth_pct: float | None = None  # 보유 중 52주 신고가 접근 비율 (%)


def _ratio_series(numer: pd.Series, denom: pd.Series) -> pd.Series:
    aligned = pd.concat([numer.rename("n"), denom.rename("d")], axis=1).dropna()
    if aligned.empty:
        return pd.Series(dtype=float)
    return aligned["n"] / aligned["d"]


def _chg(s: pd.Series, n: int) -> float | None:
    if len(s) <= n:
        return None
    return float((s.iloc[-1] / s.iloc[-1 - n] - 1) * 100)


def _interpret(chg_20d: float | None, market: str) -> str:
    if chg_20d is None:
        return ""
    if market == "US":
        # RSP/SPY: 상승 = 소형주 강세 = 폭 확장
        if chg_20d >= 2:
            return "시장 폭 확장 (소형주 강세, 건강한 추세)"
        if chg_20d <= -2:
            return "대형주 쏠림 (시장 폭 축소, 후기 강세 경계)"
        return "시장 폭 보합"
    elif market == "KR":
        if chg_20d >= 2:
            return "코스닥 강세 — 위험선호 회복"
        if chg_20d <= -2:
            return "코스피 쏠림 — 방어 심리"
        return "코스피·코스닥 균형"
    return ""


def compute_market_breadth(benchmarks: dict[str, Any], holdings_risk: list[Any]) -> MarketBreadth:
    """시장 폭 + 포트 폭 종합 계산."""
    b = MarketBreadth()

    # US: RSP / SPY (둘 다 S&P500이므로 RSP/SPY 비율이 동일가중 추세)
    rsp = benchmarks.get("S&P500 동일가중")
    spy_ix = benchmarks.get("S&P500")  # 지수 ^GSPC
    if rsp and spy_ix and not rsp.daily.empty and not spy_ix.daily.empty:
        ratio = _ratio_series(rsp.daily["Close"].dropna(), spy_ix.daily["Close"].dropna())
        if not ratio.empty:
            b.us_breadth_ratio = round(float(ratio.iloc[-1]), 4)
            b.us_breadth_chg_20d = _chg(ratio, 20)
            b.us_interpretation = _interpret(b.us_breadth_chg_20d, "US")

    # KR: KOSDAQ / KOSPI
    kosdaq = benchmarks.get("KOSDAQ")
    kospi = benchmarks.get("KOSPI")
    if kosdaq and kospi and not kosdaq.daily.empty and not kospi.daily.empty:
        ratio = _ratio_series(kosdaq.daily["Close"].dropna(), kospi.daily["Close"].dropna())
        if not ratio.empty:
            b.kr_breadth_ratio = round(float(ratio.iloc[-1]), 4)
            b.kr_breadth_chg_20d = _chg(ratio, 20)
            b.kr_interpretation = _interpret(b.kr_breadth_chg_20d, "KR")

    # 포트 폭: 보유 중 52주 신고가 접근 종목 비율
    # holdings_risk는 HoldingRisk 리스트. tech 정보가 없으므로 signal 텍스트에서 추정
    if holdings_risk:
        near_hi_count = sum(
            1 for h in holdings_risk
            if any("52주 신고가" in (s or "") for s in (h.signals or []))
        )
        b.portfolio_breadth_pct = round(near_hi_count / len(holdings_risk) * 100, 1)

    log.info(
        f"시장 폭: US={b.us_breadth_chg_20d}, KR={b.kr_breadth_chg_20d}, "
        f"포트 신고가 접근={b.portfolio_breadth_pct}%"
    )
    return b
