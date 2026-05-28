"""CFTC COT (Commitments of Traders) + 주요 ETF 자금흐름.

COT: CFTC Public Reporting API (무료, JSON).
   주간 데이터 (매주 화요일 기준, 금요일 발표).
   핵심: S&P500 e-mini의 Managed Money (헤지펀드) 순포지션.

ETF flow: yfinance Ticker.info의 totalAssets 변화로 간접 측정.
   순매수/순매도 정확치는 ETF.com 등 유료가 필요하나, AUM 변화 + 가격 효과 제거로 근사.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

import requests
import yfinance as yf

from config import get_logger

log = get_logger(__name__)


@dataclass
class COTData:
    """선물 시장의 큰 손 포지션."""
    report_date: str = ""               # 보고 기준일 (화요일)
    sp500_mm_long: int | None = None    # Managed Money long (계약수)
    sp500_mm_short: int | None = None
    sp500_mm_net: int | None = None     # long - short
    sp500_mm_net_chg_1w: int | None = None  # 1주 변화
    interpretation: str = ""


@dataclass
class ETFFlow:
    ticker: str
    name: str
    aum_usd: float | None = None
    volume_avg_20d: float | None = None
    volume_recent_5d: float | None = None
    volume_trend: float | None = None   # 5d/20d 비율
    interpretation: str = ""


# CFTC Public Reporting endpoint (Disaggregated Futures Only, Combined)
# https://publicreporting.cftc.gov/resource/72hh-3qpy.json (Disaggregated F&O)
# S&P 500 E-MINI CME contract: market_and_exchange_names contains "S&P 500"
COT_URL = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"


def fetch_cot_sp500() -> COTData:
    """CFTC COT S&P500 e-mini 헤지펀드 포지션. 최근 2주."""
    try:
        # SoQL 필터: 최근 데이터, S&P 500 e-mini만
        r = requests.get(
            COT_URL,
            params={
                "$where": "market_and_exchange_names like '%E-MINI S%P 500%'",
                "$order": "report_date_as_yyyy_mm_dd DESC",
                "$limit": "2",
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if not data or len(data) < 1:
            return COTData()

        latest = data[0]
        # Disaggregated 보고서의 Managed Money 필드명:
        # m_money_positions_long, m_money_positions_short
        mm_long = int(float(latest.get("m_money_positions_long", 0)))
        mm_short = int(float(latest.get("m_money_positions_short", 0)))
        mm_net = mm_long - mm_short

        prev_net = None
        if len(data) >= 2:
            prev = data[1]
            prev_long = int(float(prev.get("m_money_positions_long", 0)))
            prev_short = int(float(prev.get("m_money_positions_short", 0)))
            prev_net = prev_long - prev_short

        chg = (mm_net - prev_net) if prev_net is not None else None

        # 해석
        if mm_net > 100000:
            interp = "헤지펀드 극단적 매수 포지션 (역지표: 매도 압력 가능)"
        elif mm_net > 30000:
            interp = "헤지펀드 매수 우위"
        elif mm_net < -100000:
            interp = "헤지펀드 극단적 매도 포지션 (역지표: 매수 압력 가능)"
        elif mm_net < -30000:
            interp = "헤지펀드 매도 우위"
        else:
            interp = "헤지펀드 중립"

        return COTData(
            report_date=latest.get("report_date_as_yyyy_mm_dd", "")[:10],
            sp500_mm_long=mm_long,
            sp500_mm_short=mm_short,
            sp500_mm_net=mm_net,
            sp500_mm_net_chg_1w=chg,
            interpretation=interp,
        )
    except Exception as exc:
        log.debug(f"CFTC COT 수집 실패: {exc}")
        return COTData()


# 주요 ETF — 글로벌 자금흐름 대용
MAJOR_ETFS = {
    "SPY": "S&P 500",
    "QQQ": "Nasdaq 100",
    "IWM": "Russell 2000 (소형주)",
    "TLT": "20Y+ Treasury",
    "GLD": "금",
    "EEM": "신흥국 주식",
}


def fetch_etf_flows() -> list[ETFFlow]:
    """주요 ETF AUM + 거래량 추세."""
    out = []
    for ticker, name in MAJOR_ETFS.items():
        try:
            t = yf.Ticker(ticker)
            info = t.info
            aum = info.get("totalAssets")
            hist = t.history(period="1mo", interval="1d")
            if hist.empty or "Volume" not in hist:
                continue

            volumes = hist["Volume"].dropna()
            if len(volumes) < 21:
                continue
            avg_20 = float(volumes.tail(21).head(20).mean())
            recent_5 = float(volumes.tail(5).mean())
            trend = round(recent_5 / avg_20, 2) if avg_20 > 0 else None

            if trend is None:
                interp = ""
            elif trend >= 1.5:
                interp = "거래량 급증 — 자금 유입 또는 청산 활발"
            elif trend <= 0.6:
                interp = "거래량 위축 — 관심 감소"
            else:
                interp = "정상 범위"

            out.append(ETFFlow(
                ticker=ticker, name=name,
                aum_usd=float(aum) if aum else None,
                volume_avg_20d=avg_20,
                volume_recent_5d=recent_5,
                volume_trend=trend,
                interpretation=interp,
            ))
        except Exception as exc:
            log.debug(f"ETF flow 실패 ({ticker}): {exc}")
    log.info(f"ETF flow 수집: {len(out)}/{len(MAJOR_ETFS)}")
    return out
