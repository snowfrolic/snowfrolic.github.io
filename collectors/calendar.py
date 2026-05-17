"""경제 이벤트 캘린더 — FOMC·CPI·고용·한은 금통위.

데이터 소스
-----------
1. FRED Release API: CPI/PCE/PPI/고용/실업률 등 정기 발표 (API 키 필요)
2. 하드코딩된 FOMC·한은 금통위 일정 (2026년 공식 발표 기준)
3. 향후 7일 윈도우만 표시
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import requests

from config import FRED_API_KEY, get_logger

log = get_logger(__name__)


@dataclass
class EconEvent:
    when: date
    country: str   # US / KR / GLOBAL
    name: str
    importance: str  # high / mid / low
    source: str


# ── 2026년 공식 일정 (출처: 연준·한국은행 보도자료) ───────────────────────
FOMC_2026 = [
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29),
    date(2026, 6, 17), date(2026, 7, 29), date(2026, 9, 16),
    date(2026, 10, 28), date(2026, 12, 16),
]
BOK_2026 = [   # 한국은행 금융통화위원회 통화정책방향 결정회의
    date(2026, 1, 15), date(2026, 2, 26), date(2026, 4, 9),
    date(2026, 5, 28), date(2026, 7, 9), date(2026, 8, 27),
    date(2026, 10, 15), date(2026, 11, 26),
]


def _hardcoded_events(start: date, end: date) -> list[EconEvent]:
    out: list[EconEvent] = []
    for d in FOMC_2026:
        if start <= d <= end:
            out.append(EconEvent(d, "US", "FOMC 정책금리 결정", "high", "Fed"))
    for d in BOK_2026:
        if start <= d <= end:
            out.append(EconEvent(d, "KR", "한은 금통위 정책금리 결정", "high", "BOK"))
    return out


# ── FRED Release ID — 주요 거시 발표 ──────────────────────────────────────
FRED_RELEASES = {
    10: ("US", "CPI 소비자물가지수", "high"),
    50: ("US", "비농업 고용·실업률", "high"),
    21: ("US", "PCE 개인소비지출", "high"),
    151: ("US", "PPI 생산자물가지수", "mid"),
    53: ("US", "ISM 제조업 PMI", "mid"),
    175: ("US", "ADP 민간고용", "mid"),
    18: ("US", "산업생산", "low"),
}


def _fetch_fred_release_dates(start: date, end: date) -> list[EconEvent]:
    if not FRED_API_KEY:
        return []

    out: list[EconEvent] = []
    base = "https://api.stlouisfed.org/fred/release/dates"
    for rid, (country, name, importance) in FRED_RELEASES.items():
        try:
            r = requests.get(base, params={
                "release_id": rid,
                "api_key": FRED_API_KEY,
                "file_type": "json",
                "realtime_start": start.isoformat(),
                "realtime_end": end.isoformat(),
                "include_release_dates_with_no_data": "true",
            }, timeout=10)
            r.raise_for_status()
            for row in r.json().get("release_dates", []):
                d_str = row.get("date")
                if not d_str:
                    continue
                d = date.fromisoformat(d_str)
                if start <= d <= end:
                    out.append(EconEvent(d, country, name, importance, f"FRED #{rid}"))
        except Exception as exc:
            log.debug(f"FRED release {rid} 실패: {exc}")
    return out


def fetch_upcoming_events(days_ahead: int = 7) -> list[EconEvent]:
    """오늘부터 N일 후까지 예정된 이벤트, 날짜·중요도 순 정렬."""
    today = date.today()
    end = today + timedelta(days=days_ahead)
    events = _hardcoded_events(today, end) + _fetch_fred_release_dates(today, end)

    importance_rank = {"high": 0, "mid": 1, "low": 2}
    events.sort(key=lambda e: (e.when, importance_rank.get(e.importance, 9)))
    return events
