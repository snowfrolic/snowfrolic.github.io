"""일본 엔캐리 트레이드 리스크 점수.

엔캐리 = 저금리 엔으로 차입 → 고금리 자산 매수. 청산 시 글로벌 위험자산 동반 폭락.

청산 트리거 조건 (점수 가중):
1. USD/JPY 20일 변동성 (환변동성) — 25점
2. JGB 10Y 1개월 상승 (일본 금리 인상) — 25점 (월별 데이터라 변화 추정)
3. VIX 수준 (위험회피 심리) — 25점
4. 닛케이 5일 변화 (동반 신호) — 25점

총점 0-100. 60+ = 청산 임박 경계.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from config import get_logger

log = get_logger(__name__)


@dataclass
class YenCarryRisk:
    score: float = 0.0           # 0-100
    level: str = "낮음"           # 낮음 / 보통 / 높음 / 매우 높음
    usd_jpy_vol_20d: float | None = None   # USD/JPY 20일 변동성 (연환산 %)
    jgb_change_1m_bp: float | None = None  # JGB 1개월 변화 (bp)
    vix_level: float | None = None
    nikkei_chg_5d: float | None = None
    breakdown: list[str] = None   # 점수 산출 근거 텍스트

    def __post_init__(self):
        if self.breakdown is None:
            self.breakdown = []


def _annualized_vol(series: pd.Series, window: int = 20) -> float | None:
    """일일 로그수익률 표준편차 × √252 — 연환산 변동성 (%)."""
    if len(series) < window + 1:
        return None
    log_ret = np.log(series / series.shift(1)).dropna()
    if len(log_ret) < window:
        return None
    vol = float(log_ret.tail(window).std() * np.sqrt(252) * 100)
    return round(vol, 2)


def compute_yen_carry_risk(
    fx_indicators: dict,        # {"USD/JPY": PriceSeries, ...}
    macro_indicators: dict,     # {"VIX": PriceSeries, ...}
    benchmarks: dict,           # {"닛케이225": PriceSeries, ...}
    fred_data: dict,            # {"일본 10Y 국채(%, 월별)": value, ...}
) -> YenCarryRisk:
    """엔캐리 리스크 점수 계산."""
    score = 0.0
    breakdown = []
    usd_jpy_vol = None
    jgb_change_bp = None
    vix_level = None
    nikkei_5d = None

    # 1) USD/JPY 변동성
    usd_jpy = fx_indicators.get("USD/JPY")
    if usd_jpy and not usd_jpy.daily.empty:
        usd_jpy_vol = _annualized_vol(usd_jpy.daily["Close"].dropna(), 20)
        if usd_jpy_vol is not None:
            if usd_jpy_vol >= 12:
                score += 25
                breakdown.append(f"USD/JPY 20일 변동성 {usd_jpy_vol:.1f}% (높음, +25)")
            elif usd_jpy_vol >= 8:
                score += 12
                breakdown.append(f"USD/JPY 20일 변동성 {usd_jpy_vol:.1f}% (경계, +12)")
            else:
                breakdown.append(f"USD/JPY 20일 변동성 {usd_jpy_vol:.1f}% (안정)")

    # 2) JGB 10Y 변화 (FRED 월별 — 변화 추정 어려움. 절대 수준으로 대체)
    jgb_level = fred_data.get("일본 10Y 국채(%, 월별)")
    if jgb_level is not None:
        if jgb_level >= 1.5:
            score += 25
            breakdown.append(f"JGB 10Y {jgb_level:.2f}% (높음·BOJ 정상화 압박, +25)")
        elif jgb_level >= 1.0:
            score += 12
            breakdown.append(f"JGB 10Y {jgb_level:.2f}% (상승 추세, +12)")
        else:
            breakdown.append(f"JGB 10Y {jgb_level:.2f}% (낮음)")

    # 3) VIX 위험회피
    vix = macro_indicators.get("VIX")
    if vix:
        vix_level = vix.last_close
        if vix_level >= 25:
            score += 25
            breakdown.append(f"VIX {vix_level:.1f} (불안정·위험회피, +25)")
        elif vix_level >= 20:
            score += 12
            breakdown.append(f"VIX {vix_level:.1f} (경계, +12)")
        else:
            breakdown.append(f"VIX {vix_level:.1f} (안정)")

    # 4) 닛케이 5일 변화 (급락 시 엔캐리 청산 신호)
    nikkei = benchmarks.get("닛케이225")
    if nikkei and not nikkei.daily.empty:
        closes = nikkei.daily["Close"].dropna()
        if len(closes) > 5:
            nikkei_5d = float((closes.iloc[-1] / closes.iloc[-6] - 1) * 100)
            if nikkei_5d <= -5:
                score += 25
                breakdown.append(f"닛케이 5일 {nikkei_5d:+.1f}% (급락, +25)")
            elif nikkei_5d <= -3:
                score += 12
                breakdown.append(f"닛케이 5일 {nikkei_5d:+.1f}% (하락, +12)")
            else:
                breakdown.append(f"닛케이 5일 {nikkei_5d:+.1f}%")

    score = round(score, 1)
    if score >= 70:
        level = "매우 높음"
    elif score >= 50:
        level = "높음"
    elif score >= 30:
        level = "보통"
    else:
        level = "낮음"

    log.info(f"엔캐리 리스크 점수: {score}/100 ({level})")
    return YenCarryRisk(
        score=score,
        level=level,
        usd_jpy_vol_20d=usd_jpy_vol,
        jgb_change_1m_bp=jgb_change_bp,
        vix_level=vix_level,
        nikkei_chg_5d=nikkei_5d,
        breakdown=breakdown,
    )
