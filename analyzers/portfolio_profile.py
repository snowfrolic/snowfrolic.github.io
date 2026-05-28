"""포트폴리오 성향·안정성 정량 분석.

자산 배분 / 지역 배분 / 변동성 / 베타 / 다각화 점수를 계산한다.
이 결과를 AI(Gemini)에게 전달해 정성 평가까지 종합 → 대시보드 표시.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from config import get_logger

log = get_logger(__name__)


@dataclass
class PortfolioProfileData:
    total_value_krw: float = 0
    # 자산 배분 (%)
    by_asset_type: dict = field(default_factory=dict)  # {stock_kr, stock_us, etf_kr, etf_us, bond, cash, fund, pension, ...}
    # 지역 배분 (%)
    by_region: dict = field(default_factory=dict)      # {KR, US, Global, EM}
    # 추적 종목 변동성
    avg_volatility_pct: float | None = None   # 일평균 변동성 × √252
    weighted_volatility_pct: float | None = None
    # 베타 (시장 대비, 한국주는 KOSPI/미국주는 SPY)
    avg_beta: float | None = None
    # 다각화 — Herfindahl-Hirschman Index (HHI)
    hhi: float | None = None
    hhi_normalized: float | None = None  # 0-100 (낮을수록 분산 양호)
    # 상위 집중도
    top_3_concentration_pct: float | None = None
    top_holding_name: str = ""
    top_holding_pct: float | None = None
    # 통화 분산
    krw_pct: float = 0
    usd_pct: float = 0


def _classify_asset(holding_dict: dict) -> str:
    """추적 종목 → asset_type 분류."""
    market = holding_dict.get("market", "")
    name = holding_dict.get("name", "").lower()
    if any(k in name for k in ("etf", "tiger", "kodex", "kbstar", "rise", "koact", "spy", "qqq", "vti")):
        return "etf"
    if market == "KR":
        return "stock_kr"
    if market == "US":
        return "stock_us"
    return "stock"


def _classify_region(holding_dict: dict) -> str:
    market = holding_dict.get("market", "")
    if market == "KR":
        return "KR"
    if market == "US":
        return "US"
    return "Global"


def _annualized_vol(close: pd.Series, window: int = 60) -> float | None:
    if len(close) < window + 1:
        return None
    log_ret = np.log(close / close.shift(1)).dropna()
    if len(log_ret) < window:
        return None
    return float(log_ret.tail(window).std() * np.sqrt(252) * 100)


def _beta(stock_close: pd.Series, market_close: pd.Series, window: int = 60) -> float | None:
    """Cov / Var = 시장 베타."""
    if len(stock_close) < window + 1 or len(market_close) < window + 1:
        return None
    s_ret = np.log(stock_close / stock_close.shift(1)).dropna()
    m_ret = np.log(market_close / market_close.shift(1)).dropna()
    n = min(len(s_ret), len(m_ret), window)
    if n < 30:
        return None
    s_ret = s_ret.tail(n).reset_index(drop=True)
    m_ret = m_ret.tail(n).reset_index(drop=True)
    cov = np.cov(s_ret, m_ret)[0, 1]
    var = np.var(m_ret)
    if var == 0:
        return None
    return float(cov / var)


def compute_portfolio_profile(
    holdings_value: list[dict],
    non_tradeable: list,
    benchmarks: dict,
) -> PortfolioProfileData:
    """포트 정량 분석 결과."""
    profile = PortfolioProfileData()

    # 모든 자산 (추적 + 비추적) 통합
    all_items: list[tuple[str, str, float, str]] = []
    # (asset_type, region, value_krw, currency)

    for h in holdings_value:
        atype = _classify_asset(h)
        region = _classify_region(h)
        all_items.append((atype, region, h["value_krw"], h.get("currency", "KRW")))

    for h in non_tradeable:
        category = (h.category or "").strip()
        asset_type = h.asset_type or "unknown"
        if asset_type == "fund":
            atype = "fund"
        elif asset_type == "bond":
            atype = "bond"
        elif asset_type == "cash":
            atype = "cash"
        elif asset_type == "pension":
            atype = "pension"
        else:
            atype = asset_type
        region = "KR"  # 대부분 한국 펀드·연금
        if "미국채" in (h.name or ""):
            region = "US"
        elif "브라질" in (h.name or ""):
            region = "EM"
        all_items.append((atype, region, h.value_krw or 0, "KRW"))

    total = sum(v for _, _, v, _ in all_items) or 1
    profile.total_value_krw = total

    # 자산 배분
    by_asset = defaultdict(float)
    by_region = defaultdict(float)
    by_currency = defaultdict(float)
    for atype, region, v, ccy in all_items:
        by_asset[atype] += v
        by_region[region] += v
        by_currency[ccy] += v

    profile.by_asset_type = {k: round(v / total * 100, 1) for k, v in by_asset.items()}
    profile.by_region = {k: round(v / total * 100, 1) for k, v in by_region.items()}
    profile.krw_pct = round(by_currency.get("KRW", 0) / total * 100, 1)
    profile.usd_pct = round(by_currency.get("USD", 0) / total * 100, 1)

    # HHI (Herfindahl) — 개별 종목 단위
    indiv_weights = []
    for h in holdings_value:
        w = h["value_krw"] / total
        indiv_weights.append(w)
    for h in non_tradeable:
        w = (h.value_krw or 0) / total
        if w > 0:
            indiv_weights.append(w)
    hhi = sum(w ** 2 for w in indiv_weights)
    profile.hhi = round(hhi, 4)
    # HHI 정규화 (0=완벽분산, 100=한 종목 집중)
    n = len(indiv_weights)
    if n > 1:
        hhi_norm = (hhi - 1 / n) / (1 - 1 / n) * 100
        profile.hhi_normalized = round(max(0, hhi_norm), 1)

    # 상위 3 종목 집중도 + 최대 비중
    sorted_items = sorted(
        [(h["name"], h["value_krw"]) for h in holdings_value]
        + [(h.name, h.value_krw or 0) for h in non_tradeable],
        key=lambda x: x[1], reverse=True,
    )
    if sorted_items:
        top3 = sorted_items[:3]
        profile.top_3_concentration_pct = round(sum(v for _, v in top3) / total * 100, 1)
        profile.top_holding_name = sorted_items[0][0]
        profile.top_holding_pct = round(sorted_items[0][1] / total * 100, 1)

    # 변동성 + 베타 (추적 종목만)
    vols = []
    weighted_vols = []
    betas = []
    kospi = benchmarks.get("KOSPI")
    spy = benchmarks.get("S&P500")
    for h in holdings_value:
        try:
            close = h["series"].daily["Close"].dropna()
            v = _annualized_vol(close, 60)
            if v is not None:
                vols.append(v)
                weight = h["value_krw"] / total
                weighted_vols.append(v * weight)
            market_close = None
            if h["market"] == "KR" and kospi:
                market_close = kospi.daily["Close"].dropna()
            elif h["market"] == "US" and spy:
                market_close = spy.daily["Close"].dropna()
            if market_close is not None:
                b = _beta(close, market_close, 60)
                if b is not None:
                    betas.append(b)
        except Exception:
            continue

    if vols:
        profile.avg_volatility_pct = round(sum(vols) / len(vols), 2)
    if weighted_vols and holdings_value:
        # 가중 변동성 (전체 포트 대비 추적 종목만 가중)
        tracked_total = sum(h["value_krw"] for h in holdings_value) or 1
        wv = sum(v * (h["value_krw"] / tracked_total) for v, h in zip(vols, holdings_value[:len(vols)]))
        profile.weighted_volatility_pct = round(wv, 2)
    if betas:
        profile.avg_beta = round(sum(betas) / len(betas), 2)

    log.info(
        f"포트 프로필: 변동성 {profile.avg_volatility_pct}%, 베타 {profile.avg_beta}, "
        f"HHI norm {profile.hhi_normalized}, 상위 1 {profile.top_holding_pct}%"
    )
    return profile


def to_facts_dict(profile: PortfolioProfileData) -> dict:
    """AI facts 전달용 dict."""
    return {
        "asset_allocation_pct": profile.by_asset_type,
        "region_allocation_pct": profile.by_region,
        "currency_krw_pct": profile.krw_pct,
        "currency_usd_pct": profile.usd_pct,
        "avg_volatility_pct_60d": profile.avg_volatility_pct,
        "weighted_volatility_pct": profile.weighted_volatility_pct,
        "avg_beta_60d": profile.avg_beta,
        "hhi_normalized_0to100": profile.hhi_normalized,
        "top_3_concentration_pct": profile.top_3_concentration_pct,
        "top_holding": f"{profile.top_holding_name} ({profile.top_holding_pct}%)" if profile.top_holding_pct else "",
    }
