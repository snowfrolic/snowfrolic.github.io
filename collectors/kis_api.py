"""한국투자증권 KIS OpenAPI — 외국인·기관 수급 + 분봉 VWAP.

사용 전 .env에 KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO 필요.
토큰은 자동 발급 (1일 유효, 매 빌드 시 새로 발급).

API 문서: https://apiportal.koreainvestment.com
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import requests

from config import KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO, get_logger

log = get_logger(__name__)

BASE_URL = "https://openapi.koreainvestment.com:9443"
_cached_token: str = ""


def _is_available() -> bool:
    return bool(KIS_APP_KEY and KIS_APP_SECRET)


def _get_token() -> str:
    """OAuth 토큰 발급 (빌드당 1회)."""
    global _cached_token
    if _cached_token:
        return _cached_token
    try:
        r = requests.post(f"{BASE_URL}/oauth2/tokenP", json={
            "grant_type": "client_credentials",
            "appkey": KIS_APP_KEY,
            "appsecret": KIS_APP_SECRET,
        }, timeout=10)
        r.raise_for_status()
        _cached_token = r.json()["access_token"]
        log.info("KIS OAuth 토큰 발급 성공")
        return _cached_token
    except Exception as exc:
        log.warning(f"KIS 토큰 발급 실패: {exc}")
        return ""


def _headers(tr_id: str) -> dict:
    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {_get_token()}",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": tr_id,
    }


# ──────────────────────────────────────────────────────────────────
# 1) 외국인·기관 매매동향
# ──────────────────────────────────────────────────────────────────

@dataclass
class InvestorFlows:
    """일별 외국인·기관 순매수 (백만원)."""
    dates: list[str] = field(default_factory=list)
    foreign_net: list[float] = field(default_factory=list)
    inst_net: list[float] = field(default_factory=list)
    available: bool = False


def fetch_investor_flows_kis(stock_code: str, days: int = 10) -> InvestorFlows:
    """종목별 투자자별 매매동향 조회. 6자리 종목코드 (예: '005930')."""
    if not _is_available():
        return InvestorFlows()
    token = _get_token()
    if not token:
        return InvestorFlows()

    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days + 7)).strftime("%Y%m%d")

    try:
        r = requests.get(
            f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-investor",
            headers=_headers("FHKST01010900"),
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": stock_code,
                "FID_INPUT_DATE_1": start,
                "FID_INPUT_DATE_2": end,
                "FID_PERIOD_DIV_CODE": "D",
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        items = data.get("output", [])
        if not items:
            return InvestorFlows()

        dates, foreign_net, inst_net = [], [], []
        for item in items[:days]:
            dates.append(item.get("stck_bsop_date", ""))
            foreign_net.append(float(item.get("frgn_ntby_qty", 0)))
            inst_net.append(float(item.get("orgn_ntby_qty", 0)))

        return InvestorFlows(
            dates=dates, foreign_net=foreign_net, inst_net=inst_net, available=True
        )
    except Exception as exc:
        log.warning(f"KIS 투자자별 매매동향 실패 ({stock_code}): {exc}")
        return InvestorFlows()


def fetch_market_investor_flows_kis(days: int = 10) -> InvestorFlows:
    """KOSPI 시장 전체 외국인·기관 동향. 종목코드 대신 시장 코드."""
    if not _is_available():
        return InvestorFlows()
    token = _get_token()
    if not token:
        return InvestorFlows()

    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days + 14)).strftime("%Y%m%d")

    try:
        r = requests.get(
            f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-investor",
            headers=_headers("FHKST03010100"),
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": "0001",
                "FID_INPUT_DATE_1": start,
                "FID_INPUT_DATE_2": end,
                "FID_PERIOD_DIV_CODE": "D",
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        items = data.get("output", [])
        if not items:
            log.info("KIS 시장 투자자 매매동향: 데이터 없음")
            return InvestorFlows()

        dates, foreign_net, inst_net = [], [], []
        for item in items[:days]:
            dates.append(item.get("stck_bsop_date", ""))
            foreign_net.append(float(item.get("frgn_ntby_amt", 0)) / 1_000_000)
            inst_net.append(float(item.get("orgn_ntby_amt", 0)) / 1_000_000)

        log.info(f"KIS 시장 투자자 매매동향: {len(dates)}일")
        return InvestorFlows(
            dates=dates, foreign_net=foreign_net, inst_net=inst_net, available=True
        )
    except Exception as exc:
        log.warning(f"KIS 시장 투자자 매매동향 실패: {exc}")
        return InvestorFlows()


# ──────────────────────────────────────────────────────────────────
# 7) 분봉 VWAP
# ──────────────────────────────────────────────────────────────────

def fetch_vwap_kis(stock_code: str) -> float | None:
    """전일 VWAP 계산 — KIS 분봉 조회 후 volume-weighted average price."""
    if not _is_available():
        return None
    token = _get_token()
    if not token:
        return None

    # 전일 분봉 조회 (1분봉)
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    try:
        r = requests.get(
            f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
            headers=_headers("FHKST03010200"),
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": stock_code,
                "FID_INPUT_DATE_1": yesterday,
                "FID_INPUT_HOUR_1": "090000",
                "FID_PW_DATA_INCU_YN": "N",
            },
            timeout=15,
        )
        r.raise_for_status()
        items = r.json().get("output2", [])
        if not items:
            return None

        total_pv = 0.0
        total_v = 0.0
        for item in items:
            price = float(item.get("stck_prpr", 0))
            vol = float(item.get("cntg_vol", 0))
            if price > 0 and vol > 0:
                total_pv += price * vol
                total_v += vol

        if total_v == 0:
            return None
        vwap = total_pv / total_v
        return round(vwap, 2)
    except Exception as exc:
        log.debug(f"KIS VWAP 실패 ({stock_code}): {exc}")
        return None
