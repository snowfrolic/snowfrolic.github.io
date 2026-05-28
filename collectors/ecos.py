"""한국은행 ECOS Open API — 한국 거시지표.

URL 패턴: https://ecos.bok.or.kr/api/StatisticSearch/{KEY}/json/kr/{start}/{end}/{stat_code}/{cycle}/{startdate}/{enddate}/{item}

주요 통계표:
- 722Y001: 한국은행 기준금리 (M)
- 817Y002: 시장금리 (D) — 040301000 국고채 3년, 040303000 국고채 10년
- 901Y009: 소비자물가지수 CPI (M) — 0 총지수
- 901Y010: 근원 CPI (M)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import requests

from config import ECOS_API_KEY, get_logger

log = get_logger(__name__)

BASE_URL = "https://ecos.bok.or.kr/api/StatisticSearch"


@dataclass
class KoreaMacro:
    base_rate_pct: float | None = None           # 한은 기준금리 (%)
    base_rate_date: str = ""
    kgb_3y_pct: float | None = None              # 국고채 3년
    kgb_3y_date: str = ""
    kgb_10y_pct: float | None = None             # 국고채 10년
    kgb_10y_date: str = ""
    cpi: float | None = None                     # 소비자물가지수
    cpi_yoy_pct: float | None = None             # 전년동월대비 YoY
    cpi_date: str = ""
    core_cpi: float | None = None
    core_cpi_yoy_pct: float | None = None
    core_cpi_date: str = ""
    notes: list[str] = field(default_factory=list)


def _is_available() -> bool:
    return bool(ECOS_API_KEY)


def _fetch_ecos(stat_code: str, cycle: str, item_code: str, periods: int = 2) -> list[dict]:
    """ECOS 일반 호출. 최근 periods개 데이터 반환."""
    if not _is_available():
        return []
    today = datetime.now()
    if cycle == "M":
        start = (today - timedelta(days=400)).strftime("%Y%m")
        end = today.strftime("%Y%m")
    elif cycle == "D":
        start = (today - timedelta(days=30)).strftime("%Y%m%d")
        end = today.strftime("%Y%m%d")
    else:
        start = (today - timedelta(days=400)).strftime("%Y")
        end = today.strftime("%Y")

    url = f"{BASE_URL}/{ECOS_API_KEY}/json/kr/1/100/{stat_code}/{cycle}/{start}/{end}/{item_code}"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
        result = data.get("StatisticSearch", {})
        rows = result.get("row", [])
        # 최근부터 정렬 (TIME 내림차순)
        rows.sort(key=lambda x: x.get("TIME", ""), reverse=True)
        return rows[:periods + 12]  # YoY 계산을 위해 +12개월
    except Exception as exc:
        log.debug(f"ECOS 호출 실패 ({stat_code}/{item_code}): {exc}")
        return []


def _latest_value(rows: list[dict]) -> tuple[float | None, str]:
    if not rows:
        return None, ""
    try:
        val = float(rows[0].get("DATA_VALUE", ""))
        time = rows[0].get("TIME", "")
        return val, time
    except (ValueError, TypeError):
        return None, ""


def _yoy(rows: list[dict]) -> float | None:
    """가장 최근 vs 12개월 전 = YoY."""
    if len(rows) < 13:
        return None
    try:
        latest = float(rows[0].get("DATA_VALUE", ""))
        year_ago = float(rows[12].get("DATA_VALUE", ""))
        if year_ago == 0:
            return None
        return round((latest / year_ago - 1) * 100, 2)
    except (ValueError, TypeError):
        return None


def fetch_korea_macro() -> KoreaMacro:
    """한국 핵심 거시지표 묶음."""
    if not _is_available():
        log.info("ECOS_API_KEY 미설정 — 한국 거시 스킵")
        return KoreaMacro()

    out = KoreaMacro()

    # 1) 한국은행 기준금리 (월별)
    rows = _fetch_ecos("722Y001", "M", "0101000")
    out.base_rate_pct, out.base_rate_date = _latest_value(rows)

    # 2) 국고채 3년 (일별)
    rows = _fetch_ecos("817Y002", "D", "010200000")
    out.kgb_3y_pct, out.kgb_3y_date = _latest_value(rows)

    # 3) 국고채 10년 (일별)
    rows = _fetch_ecos("817Y002", "D", "010210000")
    out.kgb_10y_pct, out.kgb_10y_date = _latest_value(rows)

    # 4) CPI 총지수 (월별)
    rows = _fetch_ecos("901Y009", "M", "0")
    out.cpi, out.cpi_date = _latest_value(rows)
    out.cpi_yoy_pct = _yoy(rows)

    # 5) 근원 CPI
    rows = _fetch_ecos("901Y010", "M", "0")
    out.core_cpi, out.core_cpi_date = _latest_value(rows)
    out.core_cpi_yoy_pct = _yoy(rows)

    log.info(
        f"ECOS: 기준금리 {out.base_rate_pct}%, 국고3Y {out.kgb_3y_pct}%, 10Y {out.kgb_10y_pct}%, "
        f"CPI YoY {out.cpi_yoy_pct}%, 근원CPI YoY {out.core_cpi_yoy_pct}%"
    )
    return out
