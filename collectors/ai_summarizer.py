"""Gemini API 기반 섹션별 자연어 요약.

설계 원칙
---------
1. 한 번의 API 호출로 4개 섹션 요약을 JSON으로 일괄 수령 (비용·지연 최소화)
2. temperature=0.2 — 결정론적, 매일 비슷한 톤
3. 가드레일: 모델에 "수치는 제공된 데이터에만 의존" 명시
4. 출력 JSON 강제. 파싱 실패 시 빈 요약 (graceful skip)
5. GEMINI_API_KEY 미설정 시 모든 섹션 요약 빈 문자열로
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import requests

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


def _make_facts(
    risk: Any,
    benchmarks: dict,
    macro: Any,
    holdings_with_chg: list[dict],
) -> dict:
    """모델에 전달할 사실 데이터. 임의 수치 생성을 막기 위해 정확한 값만."""
    bench = {
        name: {
            "close": round(s.last_close, 2),
            "chg_pct": round(s.pct_change, 2),
        }
        for name, s in benchmarks.items()
    }

    fx_keys = ["USD/KRW", "USD/JPY", "EUR/USD", "DXY 달러인덱스", "VIX", "WTI 원유", "금"]
    fx_macro = {
        k: {
            "value": round(macro.indicators[k].last_close, 4),
            "chg_pct": round(macro.indicators[k].pct_change, 2),
        }
        for k in fx_keys if k in macro.indicators
    }

    yc = macro.yield_curve
    yield_curve = {
        "us_10y": round(yc.us_10y, 2) if yc.us_10y is not None else None,
        "us_3m": round(yc.us_3m, 2) if yc.us_3m is not None else None,
        "spread_10y_3m": round(yc.spread_10y_3m, 2) if yc.spread_10y_3m is not None else None,
        "inverted": yc.inverted,
    }

    # 상위/하위 종목 (관심도 높은 것만 전달)
    top_holdings = sorted(risk.holdings, key=lambda h: h.weight_pct, reverse=True)[:5]
    chg_map = {h["ticker"]: h["daily_chg"] for h in holdings_with_chg}
    holdings = [
        {
            "name": h.name,
            "weight_pct": round(h.weight_pct, 1),
            "daily_chg": round(chg_map.get(h.ticker, 0), 2),
            "composite_score": round(h.composite, 0),
            "action": h.action,
        }
        for h in top_holdings
    ]

    return {
        "portfolio_score": round(risk.overall_score, 1),
        "portfolio_action": risk.overall_action,
        "short_term_warning": risk.short_term_warning,
        "long_term_warning": risk.long_term_warning,
        "macro_notes": risk.macro_notes,
        "benchmarks": bench,
        "fx_and_macro": fx_macro,
        "yield_curve": yield_curve,
        "top_holdings": holdings,
    }


PROMPT_SYSTEM = (
    "당신은 한국 개인투자자를 위한 금융 시장 요약 작성자입니다. "
    "각 섹션마다 2-3문장의 한국어 요약을 작성합니다. "
    "엄격한 규칙: "
    "(1) 제공된 'facts' 데이터의 수치만 사용하며 새 숫자를 만들어내지 않습니다. "
    "(2) 사실 + 간결한 평가 톤. 사실은 데이터에서 직접 인용, 평가는 한 문장 이내. "
    "(3) 각 섹션은 최대 3문장, 문장당 50자 이내 권장. "
    "(4) 같은 표현 반복을 피하고 자연스러운 한국어로. "
    "(5) 출력은 반드시 JSON 객체만. 마크다운/설명 금지."
)

PROMPT_USER_TEMPLATE = """다음 데이터를 바탕으로 4개 섹션 요약을 작성하세요. 출력은 JSON.

facts = {facts_json}

작성 지침:
- "overall": 전체 시장·매크로·포트 종합 총평. 점수·액션의 의미와 주요 매크로 신호 1-2개.
- "benchmarks": 한국·미국 지수 동향과 그 의미. 어느 시장이 강세/약세인지.
- "macro": 금리·달러·VIX의 변화와 그것이 자산시장에 시사하는 바.
- "holdings": 상위 비중 종목들의 일일 변화와 평균 리스크 점수의 함의.

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
    # 코드펜스 제거
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    # 첫 { 부터 마지막 } 까지
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception as exc:
        log.warning(f"JSON 파싱 실패: {exc}")
        return None


def _validate_numbers(summary: str, facts: dict) -> str:
    """요약에 등장한 수치가 facts에 실재하는지 약한 검증.

    1.0 이상의 십진수를 추출해 facts 내 값들의 집합과 비교.
    매칭 안 되는 게 2개 이상이면 경고 로그 (제거는 안 함).
    """
    nums = re.findall(r"-?\d+(?:\.\d+)?", summary)
    if not nums:
        return summary

    fact_values = set()

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
            # 정수형 작은 수 (한자리/두자리)는 일반 표현일 수 있어 제외
            if abs(v) < 10:
                continue
            if v not in fact_values and -v not in fact_values:
                unmatched.append(n)
        except ValueError:
            continue

    if len(unmatched) >= 2:
        log.warning(f"요약에 facts와 불일치하는 수치: {unmatched}")
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
    user_prompt = PROMPT_USER_TEMPLATE.format(facts_json=json.dumps(facts, ensure_ascii=False))

    body = {
        "systemInstruction": {"parts": [{"text": PROMPT_SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "topP": 0.9,
            "maxOutputTokens": 1024,
            "responseMimeType": "application/json",
        },
    }

    url = ENDPOINT.format(model=GEMINI_MODEL)
    import time

    def _call_once():
        return requests.post(
            url,
            headers={"x-goog-api-key": GEMINI_API_KEY},
            json=body,
            timeout=45,
        )

    try:
        r = _call_once()

        # 429는 1회 재시도 (지정된 retryDelay 또는 기본 8초 후)
        if r.status_code == 429:
            err_detail = r.text[:800]
            log.warning(f"Gemini 429 (quota) — 응답: {err_detail}")
            # retry-after 헤더 또는 RetryInfo 안의 retryDelay 추출
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
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except requests.HTTPError as exc:
        body_preview = ""
        try:
            body_preview = exc.response.text[:500]
        except Exception:
            pass
        log.warning(f"Gemini HTTP 실패 ({exc.response.status_code if exc.response else '?'}): {body_preview}")
        return SectionSummaries()
    except Exception as exc:
        log.warning(f"Gemini 호출 실패: {exc}")
        return SectionSummaries()

    parsed = _parse_json_loose(text)
    if not parsed:
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
    return summaries
