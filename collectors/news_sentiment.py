"""뉴스 감성분석.

1. 구글 뉴스 RSS에서 보유 종목·시장 키워드 헤드라인 수집
2. Claude Haiku로 일괄 감성 점수화 (긍정/부정/중립)
3. ANTHROPIC_API_KEY 미설정 시 헤드라인만 표시
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime

import requests

from config import get_logger

log = get_logger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-haiku-4-5-20251001"


@dataclass
class NewsItem:
    title: str
    url: str
    source: str
    published: str  # YYYY-MM-DD HH:MM
    keyword: str    # 어떤 키워드로 검색했나
    sentiment: str = "neutral"   # positive / negative / neutral
    score: float = 0.0           # -1.0 ~ +1.0
    why: str = ""                # 모델 판단 근거 한 줄
    # 가격 반응 (v2)
    matched_ticker: str = ""     # 뉴스 keyword와 매칭된 보유 종목 ticker
    price_reaction_pct: float | None = None  # 해당 종목의 당일 변화율
    reaction_signal: str = ""    # "호재+상승" / "호재+무반응(선반영)" / "악재+방어" / "악재+급락" 등


def enrich_news_with_price(news: list[NewsItem], holdings_chg: dict[str, float]) -> None:
    """뉴스의 keyword와 보유 종목 이름·티커 매칭 → 당일 가격 변화율 결합.

    holdings_chg: {종목명 또는 ticker: daily_chg %}
    """
    for n in news:
        kw = n.keyword.lower()
        for name, chg in holdings_chg.items():
            if kw in name.lower() or name.lower() in kw:
                n.matched_ticker = name
                n.price_reaction_pct = chg
                if chg >= 2:
                    n.reaction_signal = "상승 반응"
                elif chg <= -2:
                    n.reaction_signal = "하락 반응"
                else:
                    n.reaction_signal = "약한 반응 (선반영 가능)"
                break


def _fetch_google_news(keyword: str, max_items: int = 5) -> list[NewsItem]:
    """구글 뉴스 RSS 검색. 인증 불필요."""
    q = urllib.parse.quote(keyword)
    url = f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
    except Exception as exc:
        log.debug(f"Google News 검색 실패 ({keyword}): {exc}")
        return []

    items = re.findall(
        r"<item>(.*?)</item>",
        r.text, flags=re.DOTALL,
    )[:max_items]
    out: list[NewsItem] = []
    for blob in items:
        title = _xml_field(blob, "title")
        link = _xml_field(blob, "link")
        pub = _xml_field(blob, "pubDate")
        source = _xml_field(blob, "source")
        if title:
            out.append(NewsItem(
                title=_strip_cdata(title),
                url=link,
                source=_strip_cdata(source) or "Google News",
                published=_parse_pub(pub),
                keyword=keyword,
            ))
    return out


def _xml_field(blob: str, name: str) -> str:
    m = re.search(fr"<{name}[^>]*>(.*?)</{name}>", blob, flags=re.DOTALL)
    return (m.group(1).strip() if m else "")


def _strip_cdata(s: str) -> str:
    return re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", s, flags=re.DOTALL).strip()


def _parse_pub(s: str) -> str:
    """RSS pubDate → YYYY-MM-DD HH:MM."""
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(s)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return s[:16]


def _score_with_claude(items: list[NewsItem]) -> list[NewsItem]:
    """Claude Haiku로 일괄 감성 점수화."""
    if not ANTHROPIC_API_KEY or not items:
        return items

    payload_items = [{"i": i, "title": it.title} for i, it in enumerate(items)]
    system = (
        "You are a financial news sentiment classifier. "
        "For each Korean/English headline, output JSON array of objects: "
        '{"i": index, "sentiment": "positive"|"negative"|"neutral", '
        '"score": float in [-1,1], "why": one short Korean phrase}. '
        "Focus on impact on equity/bond markets. Output ONLY the JSON array, nothing else."
    )

    body = {
        "model": CLAUDE_MODEL,
        "max_tokens": 2048,
        "system": system,
        "messages": [
            {"role": "user", "content": json.dumps(payload_items, ensure_ascii=False)}
        ],
    }

    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
            timeout=60,
        )
        r.raise_for_status()
        text = r.json()["content"][0]["text"].strip()
        # 모델 응답이 ```json...``` 같은 fence가 감싸진 경우 제거
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE)
        results = json.loads(text)
    except Exception as exc:
        log.warning(f"Claude 감성분석 실패: {exc}")
        return items

    for r_obj in results:
        try:
            idx = int(r_obj["i"])
            if 0 <= idx < len(items):
                items[idx].sentiment = r_obj.get("sentiment", "neutral")
                items[idx].score = float(r_obj.get("score", 0))
                items[idx].why = str(r_obj.get("why", ""))
        except Exception:
            continue

    log.info(f"Claude로 {len(items)}건 헤드라인 감성 점수화 완료")
    return items


def fetch_news_for_keywords(keywords: list[str], per_kw: int = 3) -> list[NewsItem]:
    """키워드 리스트로 뉴스 수집 + 감성 점수화."""
    items: list[NewsItem] = []
    for kw in keywords:
        items.extend(_fetch_google_news(kw, per_kw))

    # 중복 제거 (제목 기준)
    seen = set()
    unique: list[NewsItem] = []
    for it in items:
        key = it.title.strip()
        if key in seen:
            continue
        seen.add(key)
        unique.append(it)

    return _score_with_claude(unique)
