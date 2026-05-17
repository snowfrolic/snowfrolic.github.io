"""리스크 평가 엔진.

설계 철학
---------
각 종목에 대해 단기/중기/장기 시계별로 0–100 점수를 산출한다.
점수가 높을수록 '위험' (즉 매도 선호), 낮을수록 '기회' (매수 선호).

- 단기 (1~2주): 일봉 모멘텀, RSI, MACD, 이격도, 거래량
- 중기 (1~6개월): 일봉 이동평균 배열, 주봉 추세, 매크로 영향
- 장기 (6개월+): 주봉 이동평균, 수익률곡선, 거시지표

3개 시계 점수를 결합해 '액션 등급' L1~L5로 매핑하고,
추가 경고 신호 (단기 조정 예상 / 장기 침체 예상)는 별도 플래그로 표시.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from analyzers.technical import TechSnapshot
from collectors.macro import MacroSnapshot

ActionLevel = Literal["L1", "L2", "L3", "L4", "L5"]

ACTION_LABELS = {
    "L1": ("강한 매수", "분할 매수 추천", "#1976d2"),
    "L2": ("매수 우위", "보유 + 추가 매수 가능", "#388e3c"),
    "L3": ("중립 보유", "보유", "#616161"),
    "L4": ("일부 차익실현", "50% 매도 권장", "#f57c00"),
    "L5": ("위험 회피", "전량 매도 / 현금 비중 확대", "#d32f2f"),
}


@dataclass
class HoldingRisk:
    ticker: str
    name: str
    market: str
    weight_pct: float
    pnl_pct: float
    short_score: float   # 0-100
    mid_score: float
    long_score: float
    composite: float
    action: ActionLevel
    signals: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class PortfolioRisk:
    total_value: float
    overall_score: float
    overall_action: ActionLevel
    short_term_warning: str | None  # 단기 조정 예상 등
    long_term_warning: str | None   # 장기 침체 예상 등
    macro_notes: list[str] = field(default_factory=list)
    holdings: list[HoldingRisk] = field(default_factory=list)


def _short_term_score(t: TechSnapshot) -> tuple[float, list[str]]:
    """일봉 단기(1~2주) 리스크. 100=매도, 0=매수."""
    score = 50.0
    signals: list[str] = []

    if t.rsi14 is not None:
        if t.rsi14 >= 75:
            score += 18
            signals.append(f"RSI 과열({t.rsi14:.0f})")
        elif t.rsi14 >= 65:
            score += 8
            signals.append(f"RSI 상승 과열권({t.rsi14:.0f})")
        elif t.rsi14 <= 25:
            score -= 18
            signals.append(f"RSI 과매도({t.rsi14:.0f}) → 반등 기회")
        elif t.rsi14 <= 35:
            score -= 8
            signals.append(f"RSI 침체권({t.rsi14:.0f})")

    if t.bb_pct is not None:
        if t.bb_pct >= 1.0:
            score += 12
            signals.append("볼린저 상단 돌파(과열)")
        elif t.bb_pct >= 0.85:
            score += 5
        elif t.bb_pct <= 0.0:
            score -= 12
            signals.append("볼린저 하단 이탈(과매도)")
        elif t.bb_pct <= 0.15:
            score -= 5

    if t.macd is not None and t.macd_signal is not None:
        if t.macd < t.macd_signal and (t.macd_hist or 0) < 0:
            score += 8
            signals.append("MACD 하향 돌파")
        elif t.macd > t.macd_signal and (t.macd_hist or 0) > 0:
            score -= 8
            signals.append("MACD 상향 돌파")

    if t.disparity_20 is not None:
        if t.disparity_20 >= 110:
            score += 8
            signals.append(f"20일선 이격도 {t.disparity_20:.1f} (과열)")
        elif t.disparity_20 <= 92:
            score -= 8
            signals.append(f"20일선 이격도 {t.disparity_20:.1f} (과매도)")

    if t.volume_ratio is not None and t.volume_ratio >= 2.0 and (t.chg_1d or 0) < -2:
        score += 10
        signals.append("거래량 급증 + 하락 — 투매성 매물")

    return _clip(score), signals


def _mid_term_score(t: TechSnapshot) -> tuple[float, list[str]]:
    """중기(1~6개월) — 이동평균 배열 중심."""
    score = 50.0
    signals: list[str] = []

    close = t.close
    if t.ma20 and t.ma60 and t.ma120:
        if close > t.ma20 > t.ma60 > t.ma120:
            score -= 15
            signals.append("정배열(20>60>120) — 중기 추세 양호")
        elif close < t.ma20 < t.ma60 < t.ma120:
            score += 15
            signals.append("역배열(20<60<120) — 중기 하락 추세")

    if t.ma60 and close < t.ma60:
        score += 6
        signals.append("60일선 하회")
    if t.ma120 and close < t.ma120:
        score += 8
        signals.append("120일선 하회")

    if t.chg_20d is not None:
        if t.chg_20d <= -10:
            score += 6
            signals.append(f"20일 누적 {t.chg_20d:+.1f}%")
        elif t.chg_20d >= 15:
            score += 4

    return _clip(score), signals


def _long_term_score(t: TechSnapshot, macro: MacroSnapshot) -> tuple[float, list[str]]:
    """장기(6개월+) — 200일선·주봉·매크로."""
    score = 50.0
    signals: list[str] = []

    if t.ma200 and t.close < t.ma200:
        score += 12
        signals.append("200일선 하회 — 장기 하락 국면 가능성")
    elif t.ma200 and t.close > t.ma200 * 1.15:
        score += 6
        signals.append("200일선 +15% 위 — 장기 과열 국면")

    if t.ma_weekly_20 and t.close < t.ma_weekly_20:
        score += 5
        signals.append("주봉 20주선 하회")

    # 수익률곡선 역전 시 모든 보유에 약한 매도 압박
    yc = macro.yield_curve
    if yc.spread_10y_3m is not None:
        if yc.inverted:
            score += 10
            signals.append(f"미국 수익률곡선 역전({yc.spread_10y_3m:+.2f}bp)")
        elif yc.spread_10y_3m < 0.5:
            score += 4
            signals.append("미국 수익률곡선 평탄화")

    return _clip(score), signals


def _clip(x: float) -> float:
    return max(0.0, min(100.0, x))


def _score_to_action(score: float) -> ActionLevel:
    if score < 20:
        return "L1"
    if score < 40:
        return "L2"
    if score < 60:
        return "L3"
    if score < 80:
        return "L4"
    return "L5"


def evaluate_holding(
    ticker: str,
    name: str,
    market: str,
    weight_pct: float,
    pnl_pct: float,
    tech: TechSnapshot,
    macro: MacroSnapshot,
) -> HoldingRisk:
    s, ss = _short_term_score(tech)
    m, ms = _mid_term_score(tech)
    l, ls = _long_term_score(tech, macro)
    composite = s * 0.3 + m * 0.4 + l * 0.3

    warnings: list[str] = []
    if tech.rsi14 and tech.rsi14 >= 75 and tech.bb_pct and tech.bb_pct >= 0.9:
        warnings.append("단기 조정 예상 (과열 동반)")
    if macro.yield_curve.inverted and tech.ma200 and tech.close < tech.ma200:
        warnings.append("장기 침체 가능 신호 (수익률곡선 역전 + 200일선 하회)")

    return HoldingRisk(
        ticker=ticker,
        name=name,
        market=market,
        weight_pct=weight_pct,
        pnl_pct=pnl_pct,
        short_score=s,
        mid_score=m,
        long_score=l,
        composite=composite,
        action=_score_to_action(composite),
        signals=ss + ms + ls,
        warnings=warnings,
    )


def evaluate_portfolio(holdings: list[HoldingRisk], macro: MacroSnapshot, total_value: float) -> PortfolioRisk:
    if not holdings:
        return PortfolioRisk(
            total_value=0,
            overall_score=50,
            overall_action="L3",
            short_term_warning=None,
            long_term_warning=None,
        )

    # 비중 가중 평균
    weighted = sum(h.composite * h.weight_pct for h in holdings) / 100.0

    # 매크로 가산 — 보유 전체에 영향
    macro_notes: list[str] = []
    bonus = 0.0
    yc = macro.yield_curve
    if yc.inverted:
        bonus += 5
        macro_notes.append(f"수익률곡선 역전 ({yc.spread_10y_3m:+.2f}) — 장기 침체 경계")
    vix = macro.indicators.get("VIX")
    if vix and vix.last_close >= 25:
        bonus += 4
        macro_notes.append(f"VIX {vix.last_close:.1f} (불안정)")
    elif vix and vix.last_close >= 20:
        bonus += 2
        macro_notes.append(f"VIX {vix.last_close:.1f} (경계)")
    dxy = macro.indicators.get("DXY 달러인덱스")
    if dxy and dxy.pct_change >= 0.5:
        bonus += 1
        macro_notes.append(f"달러인덱스 +{dxy.pct_change:.2f}% — 신흥국 부담")

    overall = _clip(weighted + bonus)

    # 종합 경고
    short_warning = None
    long_warning = None
    if vix and vix.last_close >= 25 and any((h.short_score >= 70) for h in holdings):
        short_warning = "단기 조정 예상 — VIX 급등과 보유 종목 단기 과열 신호 동반"
    if yc.inverted:
        long_warning = "장기 침체 가능 — 미국 수익률곡선 역전 지속 모니터링"
    fred_payems = macro.fred.get("비농업 고용자수(천명)")
    if fred_payems is not None and fred_payems < 0:
        long_warning = (long_warning or "") + " · 비농업 고용 감소"

    return PortfolioRisk(
        total_value=total_value,
        overall_score=overall,
        overall_action=_score_to_action(overall),
        short_term_warning=short_warning,
        long_term_warning=long_warning,
        macro_notes=macro_notes,
        holdings=holdings,
    )
