"""비추적 자산 일일 가격 책정 통합 모듈.

3가지 추적 방식 지원:
  1. fixed_yield   — 확정수익률 단순 복리 (개인연금·이율보증·RP·CMA)
  2. bond_proxy    — 채권 ETF 대용 (국고채·미국채를 KODEX국고채/TLT/IEF에 매핑)
  3. kofia_fund    — 한국 공모펀드 KOFIA 기준가 (TDF 등 공모펀드만)

매핑 파일이 없거나 데이터를 못 받으면 graceful skip — value_krw 그대로 유지.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, date
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from collectors.prices import fetch_price_series
from config import ROOT, get_logger

log = get_logger(__name__)

YIELD_MAP = ROOT / "yield_map.csv"
BOND_MAP = ROOT / "bond_etf_map.csv"
FUND_MAP = ROOT / "fund_map.csv"
EXCEL_PATH = ROOT / "포트폴리오 정리.xlsx"


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s)).lower().strip()


# ──────────────────────────────────────────────────────────────────
# 1) 확정수익 — 단순 복리
# ──────────────────────────────────────────────────────────────────

def _load_yield_map() -> dict[str, float]:
    """name 정규화 → 연수익률 (소수)."""
    if not YIELD_MAP.exists():
        return {}
    try:
        df = pd.read_csv(YIELD_MAP)
        out: dict[str, float] = {}
        for _, r in df.iterrows():
            try:
                rate = float(r["annual_yield_pct"])
                out[_norm(r["name"])] = rate / 100.0
            except (ValueError, KeyError, TypeError):
                continue
        return out
    except Exception as exc:
        log.warning(f"yield_map.csv 로드 실패: {exc}")
        return {}


def _excel_baseline_date() -> date:
    """Excel 파일 수정일을 baseline으로 — 사용자가 Excel 갱신하면 자동 reset."""
    if EXCEL_PATH.exists():
        return datetime.fromtimestamp(os.path.getmtime(EXCEL_PATH)).date()
    return date.today()


def apply_fixed_yield(holding: Any, yield_map: dict[str, float], baseline: date) -> bool:
    """확정수익률 보유에 일일 복리 적용. True면 적용됨."""
    rate = yield_map.get(_norm(holding.name))
    if rate is None or holding.value_krw is None:
        return False
    days = max(0, (date.today() - baseline).days)
    factor = (1 + rate) ** days
    holding.value_krw = holding.value_krw * factor
    holding._daily_chg_pct = ((1 + rate) ** (1 / 365) - 1) * 100
    holding._update_method = "fixed_yield"
    return True


# ──────────────────────────────────────────────────────────────────
# 2) 채권 ETF 대용
# ──────────────────────────────────────────────────────────────────

def _load_bond_map() -> dict[str, str]:
    if not BOND_MAP.exists():
        return {}
    try:
        df = pd.read_csv(BOND_MAP)
        return {
            _norm(r["name"]): str(r["proxy_ticker"]).strip()
            for _, r in df.iterrows()
            if r.get("proxy_ticker") and str(r["proxy_ticker"]).strip()
        }
    except Exception as exc:
        log.warning(f"bond_etf_map.csv 로드 실패: {exc}")
        return {}


def apply_bond_proxy(holding: Any, bond_map: dict[str, str], proxy_cache: dict, baseline: date) -> bool:
    """채권 보유에 대용 ETF의 누적 변동 적용. baseline부터 오늘까지."""
    proxy = bond_map.get(_norm(holding.name))
    if not proxy or holding.value_krw is None:
        return False

    series = proxy_cache.get(proxy)
    if series is None:
        series = fetch_price_series(proxy, proxy)
        proxy_cache[proxy] = series
    if series is None or series.daily.empty:
        log.debug(f"채권 대용 ETF {proxy} 데이터 부족 — {holding.name} 명목가치 유지")
        return False

    # baseline 이후 ETF 누적 변동률
    closes = series.daily["Close"].dropna()
    # baseline 가까운 날짜 찾기
    baseline_ts = pd.Timestamp(baseline)
    try:
        # 시계열의 index가 timezone-aware일 수 있으니 정규화
        idx = closes.index
        if hasattr(idx, 'tz') and idx.tz is not None:
            idx = idx.tz_localize(None)
        closes2 = pd.Series(closes.values, index=idx)
        on_or_before = closes2[closes2.index <= baseline_ts]
        if on_or_before.empty:
            return False
        base_price = float(on_or_before.iloc[-1])
    except Exception:
        base_price = float(closes.iloc[0])

    last_price = float(closes.iloc[-1])
    cum_chg = (last_price / base_price) - 1
    holding.value_krw = holding.value_krw * (1 + cum_chg)
    holding._daily_chg_pct = float(series.pct_change)
    holding._update_method = "bond_proxy"
    holding._proxy_ticker = proxy
    return True


# ──────────────────────────────────────────────────────────────────
# 3) KOFIA 펀드 기준가
# ──────────────────────────────────────────────────────────────────

def _load_fund_map() -> dict[str, dict]:
    """name 정규화 → {fund_code, units}."""
    if not FUND_MAP.exists():
        return {}
    try:
        df = pd.read_csv(FUND_MAP)
        out: dict[str, dict] = {}
        for _, r in df.iterrows():
            code = str(r.get("fund_code", "")).strip()
            if not code or code == "nan":
                continue
            try:
                units = float(r.get("units", 0))
            except (ValueError, TypeError):
                units = 0
            out[_norm(r["name"])] = {"fund_code": code, "units": units}
        return out
    except Exception as exc:
        log.warning(f"fund_map.csv 로드 실패: {exc}")
        return {}


def fetch_kofia_nav(fund_code: str) -> float | None:
    """KOFIA 일일 기준가 조회. 펀드코드는 KR로 시작하는 12자리."""
    if not fund_code or not fund_code.startswith("KR"):
        return None
    # KOFIA 펀드 정보 일일공시 — POST endpoint
    # 참고: 이 endpoint는 시기에 따라 변경됨. 시도 후 graceful skip
    try:
        url = "https://dis.kofia.or.kr/proframeWeb/XMLSERVICES/"
        # 간단한 fallback: 펀드평가사 API 또는 KOFIA HTML 조회
        # 변액보험 펀드는 KOFIA에 없을 가능성 높음
        # MVP는 한국포스증권 등 공개 시세 페이지를 fallback으로
        r = requests.get(
            f"https://www.fundguide.net/Mu/Fund/{fund_code}",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if r.status_code != 200:
            return None
        # 기준가 패턴 검색 (페이지 구조에 따라 다름)
        m = re.search(r"기준가[^0-9]+([\d,]+\.\d+)", r.text)
        if m:
            return float(m.group(1).replace(",", ""))
    except Exception as exc:
        log.debug(f"KOFIA {fund_code} 조회 실패: {exc}")
    return None


def apply_kofia_fund(holding: Any, fund_map: dict[str, dict]) -> bool:
    """펀드 보유에 KOFIA 기준가 적용. 매핑 없거나 조회 실패 시 False."""
    info = fund_map.get(_norm(holding.name))
    if not info or holding.value_krw is None:
        return False
    nav = fetch_kofia_nav(info["fund_code"])
    if nav is None:
        return False
    units = info["units"]
    if units <= 0:
        return False
    new_value = nav * units
    holding._daily_chg_pct = ((new_value / holding.value_krw) - 1) * 100 if holding.value_krw else 0
    holding.value_krw = new_value
    holding._update_method = "kofia_fund"
    holding._fund_code = info["fund_code"]
    holding._nav = nav
    return True


# ──────────────────────────────────────────────────────────────────
# 통합 진입점
# ──────────────────────────────────────────────────────────────────

def price_non_tradeable(non_tradeable: list[Any]) -> dict[str, int]:
    """비추적 Holding 리스트 전체에 일일 가격 책정.

    반환: {update_method: count} 통계
    """
    yield_map = _load_yield_map()
    bond_map = _load_bond_map()
    fund_map = _load_fund_map()
    baseline = _excel_baseline_date()
    proxy_cache: dict = {}

    stats = {"fixed_yield": 0, "bond_proxy": 0, "kofia_fund": 0, "manual": 0}
    log.info(
        f"비추적 자산 가격 책정 — yield_map={len(yield_map)}, "
        f"bond_map={len(bond_map)}, fund_map={len(fund_map)}, baseline={baseline}"
    )

    for h in non_tradeable:
        # 기본은 명목가치 유지 (manual)
        h._update_method = "manual"
        h._daily_chg_pct = 0.0

        # 우선순위: bond_proxy > kofia_fund > fixed_yield
        if apply_bond_proxy(h, bond_map, proxy_cache, baseline):
            stats["bond_proxy"] += 1
        elif apply_kofia_fund(h, fund_map):
            stats["kofia_fund"] += 1
        elif apply_fixed_yield(h, yield_map, baseline):
            stats["fixed_yield"] += 1
        else:
            stats["manual"] += 1

    log.info(f"비추적 가격 책정 완료: {stats}")
    return stats
