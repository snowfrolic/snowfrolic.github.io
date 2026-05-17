"""Gemini API 기반 섹션별 자연어 요약.

설계 원칙
---------
1. 한 번의 API 호출로 4개 섹션 요약을 JSON으로 일괄 수령 (비용·지연 최소화)
2. temperature=0.35 — 표현 다양성과 결정성 사이 균형
3. 가드레일: 모델에 "수치는 facts 데이터에만 의존" 명시
4. 출력 JSON 강제. 파싱 실패 시 빈 요약 (graceful skip)
5. GEMINI_API_KEY 미설정 시 모든 섹션 요약 빈 문자열로

인사이트 구조 (3단계)
-------------------
모든 섹션은 다음 흐름으로 작성됨:
  Observe   — 핵심 수치 인용
  Interpret — 시장·정책·심리 함의
  Implication — 보유 포트에 대한 직접 영향 또는 경계
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import requests

from analyzers.risk import ACTION_LABELS
from config import GEMINI_API_KEY, GEMINI_MODEL, get_logger

log = get_logger(__name__)

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


@dataclass
class SectionSummaries:
    overall: str = ""        # 종합 액션 아래 — 시장 총평
    benchmarks: str = ""     # 오늘의 시장
    macro: str = ""          # 금리·환율·변동성
    holdings: str = ""       # 보유 종목 리스크

    def is_empty(self) -> bool:
        return not any([self.overall, self.benchmarks, self.macro, self.holdings])


# ──────────────────────────────────────────────────────────────────
# Facts 데이터 빌드
# ──────────────────────────────────────────────────────────────────

def _series_stats(series: Any) -> dict:
    """PriceSeries에서 컨텍스트 통계 추출 — 1일/5일/20일/MA200/52w/변동성."""
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
        # 20일 일간수익률 표준편차 × √252 (연환산 변동성, %)
        ret = closes.pct_change().tail(20)
        out["ann_vol_20d_pct"] = round(float(ret.std()) * 100 * (252 ** 0.5), 2)
    return out


def _make_facts(
    risk: Any,
    benchmarks: dict,
    macro: Any,
    holdings_with_chg: list[dict],
) -> dict:
    """모델에 전달할 사실 데이터. 추세·맥락 포함."""
    bench = {name: _series_stats(s) for name, s in benchmarks.items()}

    fx_keys = ["USD/KRW", "USD/JPY", "EUR/USD", "DXY 달러인덱스", "VIX", "WTI 원유", "금"]
    fx_macro = {
        k: _series_stats(macro.indicators[k])
        for k in fx_keys if k in macro.indicators
    }

    yc = macro.yield_curve
    yield_curve = {
        "us_10y_yield": round(yc.us_10y, 2) if yc.us_10y is not None else None,
        "us_3m_yield": round(yc.us_3m, 2) if yc.us_3m is not None else None,
        "us_30y_yield": round(yc.us_30y, 2) if yc.us_30y is not None else None,
        "spread_10y_3m_pct": round(yc.spread_10y_3m, 2) if yc.spread_10y_3m is not None else None,
        "inverted": yc.inverted,
    }

    # 포트폴리오 구성 — 통화별 비중
    total = sum(h["value_krw"] for h in holdings_with_chg) or 1
    usd_value = sum(h["value_krw"] for h in holdings_with_chg if h["currency"] == "USD")
    kr_value = total - usd_value
    portfolio_composition = {
        "total_value_krw": int(total),
        "usd_assets_pct": round(usd_value / total * 100, 1),
        "krw_assets_pct": round(kr_value / total * 100, 1),
    }

    # 보유 종목 top 5 — 핵심 신호 일부 포함
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
            "key_signals": h.signals[:3] if h.signals else [],
            "warnings": h.warnings,
        }
        for h in top_holdings
    ]

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
        "top_holdings": holdings_data,
    }


# ──────────────────────────────────────────────────────────────────
# 프롬프트 — 인사이트 3단계 구조 강제
# ──────────────────────────────────────────────────────────────────

PROMPT_SYSTEM = """당신은 한국 개인투자자를 위한 시장 분석 전문가입니다.
단순한 사실 보고가 아니라 '관찰 → 해석 → 시사점' 3단계의 인사이트를 제공합니다.

핵심 원칙:
1. 사실 정확성: facts 데이터의 수치만 인용하세요. 데이터에 없는 숫자를 만들지 마세요.
2. 3단계 구조 (모든 섹션 공통):
   - 관찰(Observe): 핵심 수치 1-2개 인용 (간결하게)
   - 해석(Interpret): 그 수치의 시장·정책·심리 함의 — 사이클의 어디인가, 왜 그런가
   - 시사점(Implication): 보유 포트(top_holdings, portfolio_composition)에 미치는 직접 영향, 또는 다음 경계해야 할 신호
3. 톤: 시장 전문가의 신중한 어조.
   - 권장 표현: "~할 가능성", "~우호적/부담", "경계 필요", "선반영 중", "압박 요인"
   - 자제할 표현: 단정적 예측 ("반드시 ~할 것이다"), 평이한 진술 ("상승했다", "하락했다"만 반복)
4. 매크로↔포트 연결을 적극 시도: 예) 금리 상승 → 장기채 ETF 보유 부담, 달러 약세 → 미국주식 KRW 환산 압박
5. 추세 컨텍스트 활용: 단일 일일 변화율보다 chg_5d_pct, chg_20d_pct, vs_ma200_pct, pct_of_52w_range를 통합 해석
6. 출력은 반드시 JSON 객체만. 마크다운/설명/코드펜스 금지."""

PROMPT_USER_TEMPLATE = """다음 facts 데이터를 바탕으로 4개 섹션 요약을 작성하세요.

facts = {facts_json}

각 섹션 작성 지침 (모두 관찰 → 해석 → 시사점 3단계 흐름):

▶ "overall" — 종합 시장 총평 (400자 내외, 8-10문장)
   가장 중요한 섹션입니다. 다음을 포함하세요:
   • 종합 점수({portfolio_score})와 액션({portfolio_action_label})이 의미하는 시장 국면
   • 매크로 핵심 신호 1-2개의 함의 (수익률곡선·VIX·달러·환율 중 가장 의미 있는 것)
   • top_holdings 중 가장 우호적·위험한 종목 1-2개 짚기
   • 다음 1-2주 모니터링 포인트 또는 트리거

▶ "benchmarks" — 오늘의 시장 (200자 내외, 5-7문장)
   • 한국과 미국 지수의 추세 (chg_5d_pct·chg_20d_pct·vs_ma200_pct 활용)
   • 시장 국면 해석 (상승 추세 / 조정 국면 / 박스권 / 변곡점 모색)
   • 어느 지역·자산이 강세이고 그 함의

▶ "macro" — 금리·환율·변동성 (200자 내외, 5-7문장)
   • 미국채 수익률곡선·VIX·달러인덱스·USD/KRW의 변화와 의미
   • 통화정책 기대치, 위험심리, 환위험에 대한 시사점
   • 포트(미국 자산 {usd_assets_pct}%) 관점에서 우호·부담 평가

▶ "holdings" — 보유 종목 리스크 (200자 내외, 5-7문장)
   • top_holdings의 일일 변화와 기술적 상태 (정배열·역배열·과열·과매도)
   • 비중·점수·신호의 패턴 — 특히 L4/L5 액션 종목의 의미
   • 보유 전략에 대한 시사점 (어느 종목을 주시·축소·유지할 것인가)

출력 JSON 스키마:
{{
  "overall": "...",
  "benchmarks": "...",
  "macro": "...",
  "holdings": "..."
}}
"""


def _parse_json_loose(text: str) -> dict | None:
    """모델 응답에서 JSON 객체 추출. 코드펜스/잡문 제거."""
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


def _validate_numbers(summary: str, facts: dict) -> str:
    """요약에 등장한 수치가 facts에 실재하는지 약한 검증."""
    nums = re.findall(r"-?\d+(?:\.\d+)?", summary)
    if not nums:
        return summary

    fact_values: set = set()

    def _walk(obj):
        if isinstance(obj, dict):
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for v in obj:
                _walk(v)
        elif isinstance(obj, (int, float)):
            fact_values.add(round(float(obj), 2))

    _walk(facts)

    unmatched = []
    for n in nums:
        try:
            v = round(float(n), 2)
            if abs(v) < 10:
                continue
            if v not in fact_values and -v not in fact_values:
                unmatched.append(n)
        except ValueError:
            continue

    if len(unmatched) >= 3:
        log.warning(f"요약에 facts와 불일치하는 수치 {len(unmatched)}건: {unmatched[:5]}")
    return summary


def generate_summaries(
    risk: Any,
    benchmarks: dict,
    macro: Any,
    holdings_with_chg: list[dict],
) -> SectionSummaries:
    """Gemini로 4개 섹션 요약 생성. 키 없으면 빈 요약."""
    if not GEMINI_API_KEY:
        log.info("GEMINI_API_KEY 미설정 — 섹션 요약 스킵")
        return SectionSummaries()

    facts = _make_facts(risk, benchmarks, macro, holdings_with_chg)
    user_prompt = PROMPT_USER_TEMPLATE.format(
        facts_json=json.dumps(facts, ensure_ascii=False),
        portfolio_score=facts["portfolio_score"],
        portfolio_action_label=facts["portfolio_action_label"],
        usd_assets_pct=facts["portfolio_composition"]["usd_assets_pct"],
    )

    body = {
        "systemInstruction": {"parts": [{"text": PROMPT_SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": 0.35,
            "topP": 0.95,
            "maxOutputTokens": 8192,
            "responseMimeType": "application/json",
            # gemini-2.5-* thinking 모델 대응: thinking 비활성화
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

    try:
        r = _call_once()

        if r.status_code == 429:
            err_detail = r.text[:800]
            log.warning(f"Gemini 429 (quota) — 응답: {err_detail}")
            delay = 8
            try:
                err_json = r.json().get("error", {})
                for d in err_json.get("details", []):
                    rd = d.get("retryDelay", "")
                    if rd.endswith("s"):
                        delay = min(60, int(float(rd[:-1])) + 2)
                        break
            except Exception:
                pass
            log.info(f"  {delay}s 후 1회 재시도...")
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
                log.warning(
                    f"Gemini 응답 텍스트 비어있음. finishReason={finish}, "
                    f"usage={usage}, 후보={cand}"
                )
                return SectionSummaries()
        except (KeyError, IndexError) as exc:
            log.warning(f"Gemini 응답 구조 이상: {exc}. raw={str(data)[:500]}")
            return SectionSummaries()
    except requests.HTTPError as exc:
        body_preview = ""
        try:
            body_preview = exc.response.text[:500]
        except Exception:
            pass
        code = exc.response.status_code if exc.response else "?"
        log.warning(f"Gemini HTTP 실패 ({code}): {body_preview}")
        return SectionSummaries()
    except Exception as exc:
        log.warning(f"Gemini 호출 실패: {exc}")
        return SectionSummaries()

    parsed = _parse_json_loose(text)
    if not parsed:
        log.warning(f"Gemini 응답 JSON 파싱 실패. 받은 텍스트(앞 500자): {text[:500]}")
        return SectionSummaries()

    def _get(k: str) -> str:
        v = parsed.get(k, "")
        if not isinstance(v, str):
            return ""
        return _validate_numbers(v.strip(), facts)

    summaries = SectionSummaries(
        overall=_get("overall"),
        benchmarks=_get("benchmarks"),
        macro=_get("macro"),
        holdings=_get("holdings"),
    )
    log.info(
        "Gemini 요약 생성 완료: "
        f"overall={len(summaries.overall)}자, benchmarks={len(summaries.benchmarks)}자, "
        f"macro={len(summaries.macro)}자, holdings={len(summaries.holdings)}자"
    )
    # 디버깅: 실제 응답 텍스트를 로그에 기록 (품질 점검용)
    log.info("--- OVERALL ---\n" + summaries.overall)
    log.info("--- BENCHMARKS ---\n" + summaries.benchmarks)
    log.info("--- MACRO ---\n" + summaries.macro)
    log.info("--- HOLDINGS ---\n" + summaries.holdings)
    return summaries
