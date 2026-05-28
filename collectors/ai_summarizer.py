"""Gemini API 기반 섹션별 자연어 요약.

설계 원칙
---------
1. 한 번의 API 호출로 4개 섹션 × 3단계(관찰/해석/시사점) 요약을 JSON 일괄 수령
2. temperature=0.35 — 표현 다양성과 결정성 사이 균형
3. 가드레일: 모델에 "수치는 facts 데이터에만 의존" 명시
4. 출력 JSON 강제. 파싱 실패 시 빈 요약 (graceful skip)
5. GEMINI_API_KEY 미설정 시 모든 섹션 요약 빈 문자열로

응답 구조
---------
각 섹션은 dict로 분리:
  {"observe": "...", "interpret": "...", "implication": "..."}
이로써 템플릿이 단계별로 별도 렌더링 가능.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import requests

from analyzers.risk import ACTION_LABELS
from config import GEMINI_API_KEY, GEMINI_MODEL, get_logger

log = get_logger(__name__)

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


@dataclass
class SectionDetail:
    """3단계 인사이트 구조."""
    observe: str = ""
    interpret: str = ""
    implication: str = ""

    @property
    def is_empty(self) -> bool:
        return not (self.observe or self.interpret or self.implication)

    @property
    def total_len(self) -> int:
        return len(self.observe) + len(self.interpret) + len(self.implication)


@dataclass
class RiskTimeframe:
    """단기/중기/장기별 리스크 평가."""
    risk_level: str = ""     # "낮음" / "보통" / "높음" / "매우 높음"
    assessment: str = ""     # 현황 평가 (2-3문장)
    action: str = ""         # 행동 의견 (1-2문장, → 형태 권고)

    @property
    def is_empty(self) -> bool:
        return not (self.assessment or self.action)


@dataclass
class RiskAssessment:
    """포트폴리오 리스크 종합 평가 — 단기/중기/장기."""
    short_term: RiskTimeframe = field(default_factory=RiskTimeframe)
    mid_term: RiskTimeframe = field(default_factory=RiskTimeframe)
    long_term: RiskTimeframe = field(default_factory=RiskTimeframe)

    @property
    def is_empty(self) -> bool:
        return self.short_term.is_empty and self.mid_term.is_empty and self.long_term.is_empty


@dataclass
class MarketOutlookTimeframe:
    """시장 전망 — 단기/중기/장기별."""
    direction: str = ""      # "강세" / "중립~강세" / "중립" / "중립~약세" / "약세"
    assessment: str = ""     # 근거 (3-4문장, 핵심 지표 인용)
    expected: str = ""       # 예상 변동 폭 (예: "S&P500 +3~5%")

    @property
    def is_empty(self) -> bool:
        return not (self.assessment or self.expected)


@dataclass
class MarketOutlook:
    """시장 자체 전망 — 보유 포트와 독립적인 시장 방향성."""
    short_term: MarketOutlookTimeframe = field(default_factory=MarketOutlookTimeframe)
    mid_term: MarketOutlookTimeframe = field(default_factory=MarketOutlookTimeframe)
    long_term: MarketOutlookTimeframe = field(default_factory=MarketOutlookTimeframe)

    @property
    def is_empty(self) -> bool:
        return self.short_term.is_empty and self.mid_term.is_empty and self.long_term.is_empty


@dataclass
class DashboardTimeframe:
    """대시보드용 시계별 통합 — 시장 + 포트 + 근거."""
    direction: str = ""           # "강세" / "중립~강세" / "중립" / "중립~약세" / "약세"
    market_view: str = ""         # 1-2문장 — 시장 단기 현황 + 주요 지표 인용
    portfolio_action: str = ""    # 1문장 — 종목명 포함 → 액션
    rationale: str = ""           # 근거 지표 2-3개 (수치 포함)

    @property
    def is_empty(self) -> bool:
        return not (self.market_view or self.portfolio_action)


@dataclass
class KeyIndicator:
    """주요 지표 한 줄."""
    category: str = ""   # "통화정책" / "심리" / "자금" / "곡선" / "엔캐리" / "환율"
    name: str = ""
    value: str = ""      # 값 + 단위
    signal: str = ""     # "↑" / "↓" / "→" / "✓"
    note: str = ""       # 짧은 해석 (10자 이내)


@dataclass
class ExecutiveDashboard:
    """통합 대시보드 — 한눈 표시용."""
    executive_summary: str = ""    # 3-4줄 종합 (시장+포트+핵심)
    short: DashboardTimeframe = field(default_factory=DashboardTimeframe)
    mid: DashboardTimeframe = field(default_factory=DashboardTimeframe)
    long: DashboardTimeframe = field(default_factory=DashboardTimeframe)
    key_indicators: list[KeyIndicator] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.executive_summary and self.short.is_empty and not self.key_indicators


@dataclass
class SectionSummaries:
    overall: SectionDetail = field(default_factory=SectionDetail)
    benchmarks: SectionDetail = field(default_factory=SectionDetail)
    macro: SectionDetail = field(default_factory=SectionDetail)
    holdings: SectionDetail = field(default_factory=SectionDetail)
    risk_assessment: RiskAssessment = field(default_factory=RiskAssessment)
    market_outlook: MarketOutlook = field(default_factory=MarketOutlook)
    dashboard: ExecutiveDashboard = field(default_factory=ExecutiveDashboard)

    def is_empty(self) -> bool:
        return all(s.is_empty for s in (self.overall, self.benchmarks, self.macro, self.holdings))


# ──────────────────────────────────────────────────────────────────
# Facts 데이터 빌드
# ──────────────────────────────────────────────────────────────────

def _series_stats(series: Any) -> dict:
    """PriceSeries에서 컨텍스트 통계 추출."""
    closes = series.daily["Close"].dropna()
    last = float(closes.iloc[-1])
    out: dict = {
        "close": round(last, 2),
        "chg_1d_pct": round(series.pct_change, 2),
    }
    if len(closes) >= 6:
        out["chg_5d_pct"] = round((last / float(closes.iloc[-6]) - 1) * 100, 2)
    if len(closes) >= 21:
        out["chg_20d_pct"] = round((last / float(closes.iloc[-21]) - 1) * 100, 2)
    if len(closes) >= 60:
        out["chg_60d_pct"] = round((last / float(closes.iloc[-60]) - 1) * 100, 2)
    if len(closes) >= 200:
        ma200 = float(closes.tail(200).mean())
        out["vs_ma200_pct"] = round((last / ma200 - 1) * 100, 2)
    if len(closes) >= 252:
        high52 = float(closes.tail(252).max())
        low52 = float(closes.tail(252).min())
        if high52 > low52:
            out["pct_of_52w_range"] = round((last - low52) / (high52 - low52) * 100, 1)
            out["52w_high"] = round(high52, 2)
            out["52w_low"] = round(low52, 2)
    if len(closes) >= 21:
        ret = closes.pct_change().tail(20)
        out["ann_vol_20d_pct"] = round(float(ret.std()) * 100 * (252 ** 0.5), 2)
    return out


def _make_facts(
    risk: Any,
    benchmarks: dict,
    macro: Any,
    holdings_with_chg: list[dict],
    yen_carry: Any = None,
    market_breadth: Any = None,
    put_call: Any = None,
    aaii: Any = None,
    krx_flows: Any = None,
    eps_revisions: list = None,
    cot: Any = None,
    etf_flows: list = None,
    korea_macro: Any = None,
) -> dict:
    """모델에 전달할 사실 데이터. FRED 거시지표 포함."""
    bench = {name: _series_stats(s) for name, s in benchmarks.items()}

    fx_keys = ["USD/KRW", "USD/JPY", "EUR/USD", "DXY 달러인덱스", "VIX", "WTI 원유", "금"]
    fx_macro = {
        k: _series_stats(macro.indicators[k])
        for k in fx_keys if k in macro.indicators
    }

    yc = macro.yield_curve
    yield_curve = {
        "us_10y_yield_pct": round(yc.us_10y, 2) if yc.us_10y is not None else None,
        "us_3m_yield_pct": round(yc.us_3m, 2) if yc.us_3m is not None else None,
        "us_30y_yield_pct": round(yc.us_30y, 2) if yc.us_30y is not None else None,
        "spread_10y_3m_pct": round(yc.spread_10y_3m, 2) if yc.spread_10y_3m is not None else None,
        "inverted": yc.inverted,
    }

    # FRED 거시지표 (CPI·실업률·고용·하이일드 등)
    fred_indicators = dict(macro.fred) if macro.fred else {}

    # 포트폴리오 구성
    total = sum(h["value_krw"] for h in holdings_with_chg) or 1
    usd_value = sum(h["value_krw"] for h in holdings_with_chg if h["currency"] == "USD")
    kr_value = total - usd_value
    portfolio_composition = {
        "total_value_krw": int(total),
        "usd_assets_pct": round(usd_value / total * 100, 1),
        "krw_assets_pct": round(kr_value / total * 100, 1),
    }

    # 보유 종목 top 5
    chg_map = {h["ticker"]: h["daily_chg"] for h in holdings_with_chg}
    top_holdings = sorted(risk.holdings, key=lambda h: h.weight_pct, reverse=True)[:5]
    holdings_data = [
        {
            "name": h.name,
            "market": h.market,
            "weight_pct": round(h.weight_pct, 1),
            "daily_chg_pct": round(chg_map.get(h.ticker, 0), 2),
            "composite_score": round(h.composite, 0),
            "action": h.action,
            "action_label": ACTION_LABELS[h.action][0],
            "short_score": round(h.short_score, 0),
            "mid_score": round(h.mid_score, 0),
            "long_score": round(h.long_score, 0),
            "key_signals": h.signals[:5] if h.signals else [],
            "warnings": h.warnings,
        }
        for h in top_holdings
    ]

    # 수급 구조 시그널 요약 (AI가 활용)
    supply_demand_signals = []
    for h in top_holdings:
        for sig in (h.signals or []):
            if any(k in sig for k in ("상대강도", "거래량 돌파", "유동성", "52주 신고가", "주도주")):
                supply_demand_signals.append(f"{h.name}: {sig}")
    supply_demand_signals = supply_demand_signals[:8]

    return {
        "portfolio_score": round(risk.overall_score, 1),
        "portfolio_action": risk.overall_action,
        "portfolio_action_label": ACTION_LABELS[risk.overall_action][0],
        "portfolio_action_recommendation": ACTION_LABELS[risk.overall_action][1],
        "short_term_warning": risk.short_term_warning,
        "long_term_warning": risk.long_term_warning,
        "macro_notes": risk.macro_notes,
        "portfolio_composition": portfolio_composition,
        "benchmarks": bench,
        "fx_and_macro": fx_macro,
        "yield_curve": yield_curve,
        "fred_indicators": fred_indicators,
        "top_holdings": holdings_data,
        "supply_demand_signals": supply_demand_signals,
        # 가중평균 시계별 점수 (0=매수 우호, 100=매도 우호)
        "portfolio_avg_short_score": round(
            sum(h.short_score * h.weight_pct for h in risk.holdings) / 100, 1
        ) if risk.holdings else 50,
        "portfolio_avg_mid_score": round(
            sum(h.mid_score * h.weight_pct for h in risk.holdings) / 100, 1
        ) if risk.holdings else 50,
        "portfolio_avg_long_score": round(
            sum(h.long_score * h.weight_pct for h in risk.holdings) / 100, 1
        ) if risk.holdings else 50,
        # v3 신규 — 시장 전망용 지표
        "yen_carry_risk": {
            "score": yen_carry.score, "level": yen_carry.level,
            "breakdown": yen_carry.breakdown,
        } if yen_carry else None,
        "market_breadth": {
            "us_chg_20d_pct": market_breadth.us_breadth_chg_20d,
            "us_interpretation": market_breadth.us_interpretation,
            "kr_chg_20d_pct": market_breadth.kr_breadth_chg_20d,
            "kr_interpretation": market_breadth.kr_interpretation,
            "portfolio_near_52w_high_pct": market_breadth.portfolio_breadth_pct,
        } if market_breadth else None,
        "put_call_ratio": {
            "total": put_call.total_pc, "interpretation": put_call.interpretation,
        } if put_call and put_call.total_pc else None,
        "aaii_sentiment": {
            "bullish_pct": aaii.bullish_pct, "bearish_pct": aaii.bearish_pct,
            "bull_bear_spread": aaii.bull_bear_spread, "interpretation": aaii.interpretation,
        } if aaii and aaii.bullish_pct else None,
        "krx_flows_recent": {
            "dates": krx_flows.dates[-5:] if krx_flows and krx_flows.dates else [],
            "kospi_foreign_net_recent": krx_flows.kospi_foreign_net[-5:] if krx_flows else [],
            "kospi_inst_net_recent": krx_flows.kospi_inst_net[-5:] if krx_flows else [],
        } if krx_flows and getattr(krx_flows, "available", False) else None,
        "eps_revisions": [
            {
                "name": e.name, "trailing": e.trailing_eps, "forward": e.forward_eps,
                "growth_pct": e.growth_pct, "direction": e.direction,
                "target_price": e.target_price,
            } for e in (eps_revisions or [])
        ],
        "cot_sp500": {
            "report_date": cot.report_date, "mm_net": cot.sp500_mm_net,
            "mm_net_chg_1w": cot.sp500_mm_net_chg_1w,
            "interpretation": cot.interpretation,
        } if cot and cot.sp500_mm_net is not None else None,
        "etf_flows": [
            {
                "ticker": f.ticker, "name": f.name,
                "volume_trend": f.volume_trend, "interpretation": f.interpretation,
            } for f in (etf_flows or []) if f.volume_trend is not None
        ],
        "korea_macro": {
            "base_rate_pct": korea_macro.base_rate_pct,
            "kgb_3y_pct": korea_macro.kgb_3y_pct,
            "kgb_10y_pct": korea_macro.kgb_10y_pct,
            "cpi_yoy_pct": korea_macro.cpi_yoy_pct,
            "core_cpi_yoy_pct": korea_macro.core_cpi_yoy_pct,
        } if korea_macro and korea_macro.base_rate_pct is not None else None,
    }


# ──────────────────────────────────────────────────────────────────
# 프롬프트 — 3단계 분리 응답 + FRED 활용 강제
# ──────────────────────────────────────────────────────────────────

PROMPT_SYSTEM = """당신은 한국 개인투자자를 위한 시장 분석 전문가입니다.
각 섹션을 '관찰 → 해석 → 시사점' 3단계로 분리해 작성합니다.

핵심 원칙:
1. 사실 정확성: facts 데이터의 수치만 인용하세요. 데이터에 없는 숫자는 만들지 마세요.
2. FRED 거시지표 적극 활용: facts['fred_indicators']에 미국 CPI·실업률·비농업 고용·하이일드 스프레드·기준금리 등이 포함됩니다.
   특히 'macro' 섹션에서는 이 거시지표를 반드시 인용하고 시장 영향을 해석하세요.
3. 매크로↔포트 연결 적극: 금리·달러·물가·고용 변화가 보유 자산(top_holdings, portfolio_composition)에 미치는 직접 영향을 짚으세요.
4. 추세 컨텍스트 활용: chg_5d/20d/60d, vs_ma200_pct, pct_of_52w_range를 통합적으로 해석.
5. 수급 구조 우선: facts['supply_demand_signals']에 상대강도·거래량 돌파·유동성·52주 신고가 시그널이 제공됩니다.
   RSI·MACD 같은 후행 보조지표보다 이 수급 시그널을 우선 해석하세요. 실제 돈의 흐름이 기술 지표보다 중요합니다.
6. 톤: 시장 전문가의 신중한 어조.
   - 권장: "~할 가능성", "~우호적/부담", "경계 필요", "선반영 중", "압박 요인"
   - 자제: 단정적 예측, 평이한 사실 재진술
7. 출력은 반드시 JSON 객체만. 마크다운/설명/코드펜스 금지.

3단계 정의:
- observe (관찰, 1-2문장): 가장 의미 있는 수치 1-2개를 정확히 인용
- interpret (해석, 2-3문장): 그 수치가 시장 사이클·정책·심리·매크로 흐름에서 무엇을 의미하는가
- implication (시사점, 2-3문장): 보유 포트에 대한 구체적 영향과 향후 1-2주 모니터링·행동 포인트"""

PROMPT_USER_TEMPLATE = """다음 facts 데이터를 바탕으로 4개 섹션의 3단계 인사이트를 작성하세요.

facts = {facts_json}

작성 지침 (각 섹션 observe/interpret/implication 3개 키 필수):

▶ overall — 종합 시장 총평 (3단계 합 400자 내외)
   가장 중요한 섹션. observe(관찰)는 종합 점수·핵심 매크로 1개,
   interpret(해석)는 시장 국면 진단, implication(시사점)는 1-2주 핵심 모니터링 포인트.

▶ benchmarks — 오늘의 시장 (3단계 합 200자 내외)
   한·미 지수 추세 + 어느 시장 강세 + 포트 영향.

▶ macro — 금리·환율·변동성 (3단계 합 250자 내외) ⭐ FRED 데이터 반드시 인용
   yield_curve, fx_and_macro, fred_indicators 셋 모두 활용.
   특히 fred_indicators의 미국 CPI/실업률/비농업/하이일드 스프레드를 최소 1개 이상 인용해
   통화정책·경기 사이클 함의 짚기.

▶ holdings — 보유 종목 리스크 (3단계 합 200자 내외)
   top_holdings의 기술적 상태 + 비중·점수 패턴 + 보유 전략 시사점.

▶ dashboard — ⭐⭐⭐ 통합 대시보드 (최상단 한눈 표시용 — 가장 중요)
   사이트 최상단에 시장+포트+핵심 한눈 표시. 모든 지표를 가장 압축해 인사이트화.

   {{ "executive_summary": "..." }} — 3-4줄.
     첫 줄: 시장 현황 (주요 지표 2-3개 인용)
     둘째 줄: 보유 포트 영향 (구체 종목 1-2개)
     셋째 줄: 단기/중기/장기 핵심 한 줄씩 (또는 가장 중요한 1가지)

   {{ "short" / "mid" / "long" }} — 단/중/장기 각각:
     direction: "강세"/"중립~강세"/"중립"/"중립~약세"/"약세"
     market_view: 1-2문장. 해당 시계의 시장 방향성 (지표 1-2개 수치 인용)
     portfolio_action: 1문장. "→ {{구체 종목}} {{액션}}" 형태
     rationale: 1문장. 근거 지표 2-3개 나열 (예: "VIX 16↓·Put/Call 1.23↑·외국인 -342만주")

   {{ "key_indicators" }} — 8-12개 핵심 지표 배열. 각 항목:
     category: "통화정책"/"심리"/"자금"/"곡선"/"엔캐리"/"환율"/"인플레"
     name: 지표명 (짧게)
     value: 값+단위 (예: "3.64%", "16.4", "-342만주")
     signal: "↑"/"↓"/"→"/"✓"/"⚠"
     note: 10자 이내 해석 (예: "비둘기 기조", "단기 과열")

   선정 우선순위: Fed금리·한은금리·TIPS·VIX·Put/Call·외국인수급·10Y-3M·엔캐리·USD/KRW 등 시계별 결정적 지표.

▶ risk_assessment — (기존 유지, 호환성) 포트폴리오 리스크 종합 평가
   모든 지표(매크로·기술적·수급 구조·보유 구성)를 종합 판단해서 단기/중기/장기별 리스크 의견.

   데이터 참고: portfolio_avg_short_score(단기 {portfolio_avg_short_score}점),
   portfolio_avg_mid_score(중기 {portfolio_avg_mid_score}점),
   portfolio_avg_long_score(장기 {portfolio_avg_long_score}점) — 0=매수 우호, 100=매도 우호.

   각 시계(short_term/mid_term/long_term)마다:
   - risk_level: "낮음" / "보통" / "높음" / "매우 높음" (위 점수 + 모든 지표 종합 판단)
   - assessment: 보유 자산 구성 기반으로 왜 그 리스크 수준인지 (3-4문장).
     다음 지표를 종합 활용 (supply_demand_signals + key_signals에 시그널 텍스트 제공됨):
       · 상대강도(RS): 시장/섹터 대비 어느 종목이 강하고 약한가
       · 거래량 돌파: 수급 유입(bullish) 또는 투매(bearish) 이벤트
       · 유동성 추세: 거래대금 확대/위축
       · VWAP: 당일 수급 기준가 대비 위치 (위=수급 양호, 아래=약세)
       · 52주 신고가: 매물 부담 적은 구간 접근 여부
       · 외국인·기관 수급: 순매수/순매도 추세 (있으면 인용)
       · 매크로: CPI·금리·환율·VIX·수익률곡선 함의
       · 숏 인터레스트: 미국 종목의 공매도 비율 (있으면 인용)
     반드시 구체적 종목명(삼성전자, SK하이닉스 등)을 인용.
     이슈가 있는 지표와 종목을 명시적으로 짚기 (예: "삼성전자는 RSI 72 과열+섹터 내 강세지만 VWAP 하회", "TIGER회사채는 상대강도 -24%p로 시장 대비 열위").
     "일부 종목" "주요 종목" 같은 불특정 표현은 절대 사용 금지.
   - action: 종목명을 포함한 구체적 행동 의견 (1-2문장). "→" 로 시작.
     예: "→ 삼성전자·SK하이닉스 급등 시 일부 차익 실현, TIGER회사채 비중 축소 검토"

▶ market_outlook — 시장 자체 전망 (보유 포트와 독립) ⭐⭐ 사용자 핵심 요청
   "시장이 단기/중기/장기에 어떻게 갈 것인가"를 종합 데이터로 예측.
   facts의 모든 지표를 활용:
     · 통화정책: fred_indicators(미국 기준금리, TIPS 기대인플레, PCE Core, Fed 대차대조표, M2), korea_macro(한은 기준금리, 국고채 3Y/10Y, 한국 CPI YoY)
     · 신용·유동성: 하이일드 스프레드, 수익률곡선
     · 시장 심리: VIX, put_call_ratio, aaii_sentiment
     · 자금 흐름: krx_flows_recent (외국인·기관), etf_flows (SPY·QQQ·TLT·GLD 등 거래량 추세), cot_sp500 (헤지펀드 포지션)
     · 펀더멘털: eps_revisions (forward vs trailing EPS — upward/downward revision)
     · 시장 폭: market_breadth (US/KR)
     · 엔캐리: yen_carry_risk (글로벌 위험자산 청산 위험)
     · 경기: 비농업·실업·ISM PMI, 일본 CPI/금리

   각 시계(short_term/mid_term/long_term)마다:
   - direction: "강세" / "중립~강세" / "중립" / "중립~약세" / "약세"
   - assessment: 3-4문장. 위 지표 중 최소 4-5개를 구체적 수치와 함께 인용해 종합 진단.
     예: "VIX 18.2 안정, Put/Call 0.95 중립, 시장 폭(RSP/SPY) +1.2% 확장 중. Fed Funds Futures가 9월 -25bp 인하 70% 반영. 다만 RSI 70 과열·52주 +97% 위치로 단기 조정 가능성."
   - expected: 구체적 변동 폭 예상. 예: "S&P500 박스권 +0~3%", "KOSPI +3~5% 완만한 상승", "변동성 확대, -5% 조정 가능".</li>

출력 JSON 스키마 (정확히 이 구조):
{{
  "dashboard": {{
    "executive_summary": "...",
    "short": {{"direction": "...", "market_view": "...", "portfolio_action": "...", "rationale": "..."}},
    "mid":   {{"direction": "...", "market_view": "...", "portfolio_action": "...", "rationale": "..."}},
    "long":  {{"direction": "...", "market_view": "...", "portfolio_action": "...", "rationale": "..."}},
    "key_indicators": [
      {{"category": "...", "name": "...", "value": "...", "signal": "...", "note": "..."}},
      ...
    ]
  }},
  "overall":    {{"observe": "...", "interpret": "...", "implication": "..."}},
  "benchmarks": {{"observe": "...", "interpret": "...", "implication": "..."}},
  "macro":      {{"observe": "...", "interpret": "...", "implication": "..."}},
  "holdings":   {{"observe": "...", "interpret": "...", "implication": "..."}},
  "risk_assessment": {{
    "short_term": {{"risk_level": "...", "assessment": "...", "action": "..."}},
    "mid_term":   {{"risk_level": "...", "assessment": "...", "action": "..."}},
    "long_term":  {{"risk_level": "...", "assessment": "...", "action": "..."}}
  }},
  "market_outlook": {{
    "short_term": {{"direction": "...", "assessment": "...", "expected": "..."}},
    "mid_term":   {{"direction": "...", "assessment": "...", "expected": "..."}},
    "long_term":  {{"direction": "...", "assessment": "...", "expected": "..."}}
  }}
}}
"""


def _parse_json_loose(text: str) -> dict | None:
    """모델 응답에서 JSON 객체 추출."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception as exc:
        log.warning(f"JSON 파싱 실패: {exc}")
        return None


def _extract_detail(section_obj: Any) -> SectionDetail:
    """모델 응답에서 SectionDetail 객체로 추출.

    예상: {"observe": "...", "interpret": "...", "implication": "..."}
    예외 케이스 (문자열로 통째 응답한 경우): 그대로 observe에 넣음.
    """
    if isinstance(section_obj, dict):
        return SectionDetail(
            observe=str(section_obj.get("observe", "")).strip(),
            interpret=str(section_obj.get("interpret", "")).strip(),
            implication=str(section_obj.get("implication", "")).strip(),
        )
    if isinstance(section_obj, str):
        return SectionDetail(observe=section_obj.strip())
    return SectionDetail()


def generate_summaries(
    risk: Any,
    benchmarks: dict,
    macro: Any,
    holdings_with_chg: list[dict],
    yen_carry: Any = None,
    market_breadth: Any = None,
    put_call: Any = None,
    aaii: Any = None,
    krx_flows: Any = None,
    eps_revisions: list = None,
    cot: Any = None,
    etf_flows: list = None,
    korea_macro: Any = None,
) -> SectionSummaries:
    """Gemini로 4개 섹션 × 3단계 요약 + 리스크 평가 + 시장 전망 생성."""
    if not GEMINI_API_KEY:
        log.info("GEMINI_API_KEY 미설정 — 섹션 요약 스킵")
        return SectionSummaries()

    facts = _make_facts(
        risk, benchmarks, macro, holdings_with_chg,
        yen_carry=yen_carry, market_breadth=market_breadth,
        put_call=put_call, aaii=aaii, krx_flows=krx_flows,
        eps_revisions=eps_revisions, cot=cot, etf_flows=etf_flows,
        korea_macro=korea_macro,
    )
    user_prompt = PROMPT_USER_TEMPLATE.format(
        facts_json=json.dumps(facts, ensure_ascii=False, default=str),
        portfolio_avg_short_score=facts.get("portfolio_avg_short_score", 50),
        portfolio_avg_mid_score=facts.get("portfolio_avg_mid_score", 50),
        portfolio_avg_long_score=facts.get("portfolio_avg_long_score", 50),
    )

    body = {
        "systemInstruction": {"parts": [{"text": PROMPT_SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": 0.35,
            "topP": 0.95,
            "maxOutputTokens": 8192,
            "responseMimeType": "application/json",
            # gemini-2.5-* thinking 모델 대응
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }

    url = ENDPOINT.format(model=GEMINI_MODEL)
    import time

    def _call_once():
        return requests.post(
            url,
            headers={"x-goog-api-key": GEMINI_API_KEY},
            json=body,
            timeout=60,
        )

    # 일시적 에러 재시도: 429(quota) + 5xx(서버 일시 장애)
    RETRY_STATUSES = (429, 500, 502, 503, 504)
    MAX_RETRIES = 3
    try:
        r = _call_once()

        retry_count = 0
        while r.status_code in RETRY_STATUSES and retry_count < MAX_RETRIES:
            retry_count += 1
            # backoff: 5s, 15s, 45s (또는 retryDelay 따라가기)
            delay = 5 * (3 ** (retry_count - 1))
            try:
                err_json = r.json().get("error", {})
                for d in err_json.get("details", []):
                    rd = d.get("retryDelay", "")
                    if rd.endswith("s"):
                        delay = min(60, int(float(rd[:-1])) + 2)
                        break
            except Exception:
                pass
            log.warning(
                f"Gemini {r.status_code} (재시도 {retry_count}/{MAX_RETRIES}, {delay}s 후) — "
                f"응답: {r.text[:300]}"
            )
            time.sleep(delay)
            r = _call_once()

        r.raise_for_status()
        data = r.json()

        try:
            cand = data["candidates"][0]
            finish = cand.get("finishReason", "?")
            parts = cand.get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts)
            if not text:
                usage = data.get("usageMetadata", {})
                log.warning(f"Gemini 응답 비어있음. finishReason={finish}, usage={usage}")
                return SectionSummaries()
        except (KeyError, IndexError) as exc:
            log.warning(f"Gemini 응답 구조 이상: {exc}. raw={str(data)[:500]}")
            return SectionSummaries()
    except requests.HTTPError as exc:
        # exc.response truthy check is wrong: Response.__bool__ is False for 4xx/5xx.
        # Must use `is not None`.
        code = "?"
        body_preview = ""
        if exc.response is not None:
            code = exc.response.status_code
            try:
                body_preview = exc.response.text[:500]
            except Exception:
                pass
        log.warning(f"Gemini HTTP 실패 ({code}): {body_preview or '(empty body)'}")
        return SectionSummaries()
    except Exception as exc:
        log.warning(f"Gemini 호출 실패 ({type(exc).__name__}): {exc}")
        return SectionSummaries()

    parsed = _parse_json_loose(text)
    if not parsed:
        log.warning(f"Gemini 응답 JSON 파싱 실패. 앞 500자: {text[:500]}")
        return SectionSummaries()

    # risk_assessment 파싱
    ra_raw = parsed.get("risk_assessment", {})
    ra = RiskAssessment()
    for tf_key, tf_attr in [("short_term", "short_term"), ("mid_term", "mid_term"), ("long_term", "long_term")]:
        tf_data = ra_raw.get(tf_key, {})
        if isinstance(tf_data, dict):
            setattr(ra, tf_attr, RiskTimeframe(
                risk_level=str(tf_data.get("risk_level", "")).strip(),
                assessment=str(tf_data.get("assessment", "")).strip(),
                action=str(tf_data.get("action", "")).strip(),
            ))

    # market_outlook 파싱
    mo_raw = parsed.get("market_outlook", {})
    mo = MarketOutlook()
    for tf_key in ("short_term", "mid_term", "long_term"):
        tf_data = mo_raw.get(tf_key, {})
        if isinstance(tf_data, dict):
            setattr(mo, tf_key, MarketOutlookTimeframe(
                direction=str(tf_data.get("direction", "")).strip(),
                assessment=str(tf_data.get("assessment", "")).strip(),
                expected=str(tf_data.get("expected", "")).strip(),
            ))

    # dashboard 파싱 (NEW - 통합 대시보드)
    db_raw = parsed.get("dashboard", {})
    db = ExecutiveDashboard()
    if isinstance(db_raw, dict):
        db.executive_summary = str(db_raw.get("executive_summary", "")).strip()
        for tf_key in ("short", "mid", "long"):
            tf_data = db_raw.get(tf_key, {})
            if isinstance(tf_data, dict):
                setattr(db, tf_key, DashboardTimeframe(
                    direction=str(tf_data.get("direction", "")).strip(),
                    market_view=str(tf_data.get("market_view", "")).strip(),
                    portfolio_action=str(tf_data.get("portfolio_action", "")).strip(),
                    rationale=str(tf_data.get("rationale", "")).strip(),
                ))
        ki_raw = db_raw.get("key_indicators", [])
        if isinstance(ki_raw, list):
            db.key_indicators = [
                KeyIndicator(
                    category=str(k.get("category", "")).strip(),
                    name=str(k.get("name", "")).strip(),
                    value=str(k.get("value", "")).strip(),
                    signal=str(k.get("signal", "")).strip(),
                    note=str(k.get("note", "")).strip(),
                ) for k in ki_raw if isinstance(k, dict)
            ]

    summaries = SectionSummaries(
        overall=_extract_detail(parsed.get("overall")),
        benchmarks=_extract_detail(parsed.get("benchmarks")),
        macro=_extract_detail(parsed.get("macro")),
        holdings=_extract_detail(parsed.get("holdings")),
        risk_assessment=ra,
        market_outlook=mo,
        dashboard=db,
    )
    log.info(
        "Gemini 요약 생성 완료: "
        f"dashboard={'OK' if not db.is_empty else 'empty'} (exec={len(db.executive_summary)}자, ki={len(db.key_indicators)}개), "
        f"overall={summaries.overall.total_len}자, benchmarks={summaries.benchmarks.total_len}자, "
        f"macro={summaries.macro.total_len}자, holdings={summaries.holdings.total_len}자, "
        f"risk_assessment={'OK' if not ra.is_empty else 'empty'}, "
        f"market_outlook={'OK' if not mo.is_empty else 'empty'}"
    )
    # 디버그: 평문 로그
    for name, s in [("OVERALL", summaries.overall), ("BENCHMARKS", summaries.benchmarks),
                    ("MACRO", summaries.macro), ("HOLDINGS", summaries.holdings)]:
        log.info(f"--- {name} ---\n[관찰] {s.observe}\n[해석] {s.interpret}\n[시사점] {s.implication}")
    if not ra.is_empty:
        for tf_name, tf in [("단기", ra.short_term), ("중기", ra.mid_term), ("장기", ra.long_term)]:
            log.info(f"--- RISK {tf_name} [{tf.risk_level}] ---\n{tf.assessment}\n→ {tf.action}")
    if not mo.is_empty:
        for tf_name, tf in [("단기", mo.short_term), ("중기", mo.mid_term), ("장기", mo.long_term)]:
            log.info(f"--- MARKET {tf_name} [{tf.direction}] ---\n{tf.assessment}\n예상: {tf.expected}")

    return summaries
