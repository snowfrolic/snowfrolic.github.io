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
class SectionSummaries:
    overall: SectionDetail = field(default_factory=SectionDetail)
    benchmarks: SectionDetail = field(default_factory=SectionDetail)
    macro: SectionDetail = field(default_factory=SectionDetail)
    holdings: SectionDetail = field(default_factory=SectionDetail)

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

출력 JSON 스키마 (정확히 이 구조):
{{
  "overall":    {{"observe": "...", "interpret": "...", "implication": "..."}},
  "benchmarks": {{"observe": "...", "interpret": "...", "implication": "..."}},
  "macro":      {{"observe": "...", "interpret": "...", "implication": "..."}},
  "holdings":   {{"observe": "...", "interpret": "...", "implication": "..."}}
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
) -> SectionSummaries:
    """Gemini로 4개 섹션 × 3단계 요약 생성."""
    if not GEMINI_API_KEY:
        log.info("GEMINI_API_KEY 미설정 — 섹션 요약 스킵")
        return SectionSummaries()

    facts = _make_facts(risk, benchmarks, macro, holdings_with_chg)
    user_prompt = PROMPT_USER_TEMPLATE.format(
        facts_json=json.dumps(facts, ensure_ascii=False),
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

    summaries = SectionSummaries(
        overall=_extract_detail(parsed.get("overall")),
        benchmarks=_extract_detail(parsed.get("benchmarks")),
        macro=_extract_detail(parsed.get("macro")),
        holdings=_extract_detail(parsed.get("holdings")),
    )
    log.info(
        "Gemini 요약 생성 완료: "
        f"overall={summaries.overall.total_len}자, benchmarks={summaries.benchmarks.total_len}자, "
        f"macro={summaries.macro.total_len}자, holdings={summaries.holdings.total_len}자"
    )
    # 디버그: 평문 로그
    for name, s in [("OVERALL", summaries.overall), ("BENCHMARKS", summaries.benchmarks),
                    ("MACRO", summaries.macro), ("HOLDINGS", summaries.holdings)]:
        log.info(f"--- {name} ---\n[관찰] {s.observe}\n[해석] {s.interpret}\n[시사점] {s.implication}")

    return summaries
