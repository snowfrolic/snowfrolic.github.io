"""시장 심리 지표 — Put/Call Ratio, AAII Sentiment Survey.

Put/Call: CBOE 일별 CSV
AAII: 주간 조사 결과 (Bull/Bear/Neutral %)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from io import StringIO

import pandas as pd
import requests

from config import get_logger

log = get_logger(__name__)


@dataclass
class PutCallRatio:
    date: str = ""
    total_pc: float | None = None        # 전체 Put/Call (옵션 거래량)
    equity_pc: float | None = None       # 주식 옵션 Put/Call
    index_pc: float | None = None        # 지수 옵션 Put/Call
    interpretation: str = ""


@dataclass
class AAIISentiment:
    date: str = ""
    bullish_pct: float | None = None
    neutral_pct: float | None = None
    bearish_pct: float | None = None
    bull_bear_spread: float | None = None
    interpretation: str = ""


def fetch_put_call_ratio() -> PutCallRatio:
    """CBOE 일별 Put/Call Ratio. 최근 영업일."""
    url = "https://cdn.cboe.com/api/global/us_indices/daily_prices/PCRATIOS_S_history.json"
    try:
        # 메인 시도: JSON endpoint (가벼움)
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if r.status_code == 200:
            data = r.json()
            # 형식: [[timestamp, total_pc, equity_pc, ...], ...]
            if isinstance(data, list) and data:
                latest = data[-1]
                return _parse_pc(latest)
    except Exception as exc:
        log.debug(f"CBOE JSON Put/Call 실패: {exc}")

    # 폴백: CSV
    try:
        csv_url = "https://cdn.cboe.com/resources/options/volume_and_call_put_ratios/indexpcarchive.csv"
        r = requests.get(csv_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
        df = pd.read_csv(StringIO(r.text), skiprows=2)
        if df.empty:
            return PutCallRatio()
        latest = df.iloc[-1]
        total_pc = float(latest.get("P/C Ratio", 0)) if "P/C Ratio" in df.columns else None
        return PutCallRatio(
            date=str(latest.get("Date", "")),
            total_pc=total_pc,
            interpretation=_interpret_pc(total_pc),
        )
    except Exception as exc:
        log.warning(f"CBOE Put/Call 수집 실패: {exc}")
        return PutCallRatio()


def _parse_pc(row: list | dict) -> PutCallRatio:
    """CBOE JSON 응답 한 행 파싱."""
    try:
        if isinstance(row, dict):
            total_pc = float(row.get("total_pc", row.get("total", 0)))
        else:
            total_pc = float(row[1]) if len(row) > 1 else None
        return PutCallRatio(
            date=datetime.now().strftime("%Y-%m-%d"),
            total_pc=total_pc,
            interpretation=_interpret_pc(total_pc),
        )
    except Exception:
        return PutCallRatio()


def _interpret_pc(pc: float | None) -> str:
    if pc is None:
        return ""
    if pc >= 1.2:
        return "극단적 비관 (역지표: 반등 가능)"
    if pc >= 1.0:
        return "비관 우위"
    if pc >= 0.7:
        return "중립"
    if pc >= 0.5:
        return "낙관 우위"
    return "극단적 낙관 (역지표: 조정 경계)"


def fetch_aaii_sentiment() -> AAIISentiment:
    """AAII Sentiment Survey 주간 결과.

    공식 데이터 페이지에서 스크래핑. 크롤링 실패 시 빈 값.
    """
    url = "https://www.aaii.com/sentimentsurvey/sent_results"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        if r.status_code != 200:
            return AAIISentiment()
        text = r.text

        # 페이지의 가장 최근 주간 결과 추출 (패턴 매칭)
        # 형식 예: "Bullish 45.5%, Neutral 30.0%, Bearish 24.5%"
        bull_match = re.search(r"Bullish[^0-9]*(\d+\.\d+)", text, re.IGNORECASE)
        neut_match = re.search(r"Neutral[^0-9]*(\d+\.\d+)", text, re.IGNORECASE)
        bear_match = re.search(r"Bearish[^0-9]*(\d+\.\d+)", text, re.IGNORECASE)

        bull = float(bull_match.group(1)) if bull_match else None
        neut = float(neut_match.group(1)) if neut_match else None
        bear = float(bear_match.group(1)) if bear_match else None

        spread = (bull - bear) if (bull is not None and bear is not None) else None

        return AAIISentiment(
            date=datetime.now().strftime("%Y-%m-%d"),
            bullish_pct=bull,
            neutral_pct=neut,
            bearish_pct=bear,
            bull_bear_spread=spread,
            interpretation=_interpret_aaii(spread),
        )
    except Exception as exc:
        log.debug(f"AAII Sentiment 실패: {exc}")
        return AAIISentiment()


def _interpret_aaii(spread: float | None) -> str:
    """Bull-Bear Spread 해석 (역지표)."""
    if spread is None:
        return ""
    if spread >= 30:
        return "극단적 낙관 (역지표: 조정 경계)"
    if spread >= 10:
        return "낙관 우위"
    if spread >= -10:
        return "중립"
    if spread >= -30:
        return "비관 우위"
    return "극단적 비관 (역지표: 반등 가능)"
