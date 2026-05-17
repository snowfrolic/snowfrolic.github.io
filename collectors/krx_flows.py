"""KRX 외국인·기관 수급 동향.

KRX 공식 정보데이터시스템(data.krx.co.kr)의 OTP API를 직접 호출.
회원가입·로그인 불필요. pykrx보다 안정적.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from io import BytesIO

import pandas as pd
import requests

from config import get_logger

log = get_logger(__name__)

OTP_URL = "http://data.krx.co.kr/comm/fileDn/GenerateOTP/generate.cmd"
DOWNLOAD_URL = "http://data.krx.co.kr/comm/fileDn/download_csv/download.cmd"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/",
}


@dataclass
class KrxFlows:
    kospi_foreign_net: list[float]   # 일별 외국인 순매수 (백만원)
    kospi_inst_net: list[float]
    kosdaq_foreign_net: list[float]
    kosdaq_inst_net: list[float]
    dates: list[str]
    by_ticker: dict[str, dict]
    available: bool = True


def _empty(reason: str = "") -> KrxFlows:
    if reason:
        log.warning(f"KRX 데이터 비활성: {reason}")
    return KrxFlows([], [], [], [], [], {}, available=False)


def _fetch_csv(params: dict) -> pd.DataFrame:
    """OTP → CSV 다운로드. 실패 시 빈 DataFrame."""
    try:
        otp = requests.post(OTP_URL, data=params, headers=HEADERS, timeout=15).text
        if not otp or len(otp) < 10:
            return pd.DataFrame()
        csv = requests.post(DOWNLOAD_URL, data={"code": otp}, headers=HEADERS, timeout=20).content
        # KRX는 EUC-KR/CP949 인코딩
        return pd.read_csv(BytesIO(csv), encoding="cp949")
    except Exception as exc:
        log.warning(f"KRX OTP/다운로드 실패: {exc}")
        return pd.DataFrame()


def _fetch_market_trading_by_date(market_id: str, days: int = 10) -> pd.DataFrame:
    """투자자별 거래실적 (시장별, 일별).

    market_id: "STK" (KOSPI) / "KSQ" (KOSDAQ)
    """
    today = datetime.now()
    start = today - timedelta(days=days + 14)
    params = {
        "bld": "dbms/MDC/STAT/standard/MDCSTAT02301",  # 투자자별 거래실적(일별)
        "mktId": market_id,
        "strtDd": start.strftime("%Y%m%d"),
        "endDd": today.strftime("%Y%m%d"),
        "trdVolVal": "2",   # 1=거래량, 2=거래대금
        "askBid": "3",      # 1=매수, 2=매도, 3=순매수
        "share": "1",
        "money": "3",       # 3=백만원 단위
        "csvxls_isNo": "false",
    }
    return _fetch_csv(params)


def fetch_market_flows(days: int = 10) -> KrxFlows:
    """KOSPI/KOSDAQ 외국인·기관 일별 순매수."""
    kospi_df = _fetch_market_trading_by_date("STK", days)
    kosdaq_df = _fetch_market_trading_by_date("KSQ", days)

    if kospi_df.empty:
        return _empty("KRX 데이터 응답 없음")

    # 컬럼명: "일자", "기관합계", "기타법인", "개인", "외국인합계", "전체"
    date_col = next((c for c in kospi_df.columns if "일자" in c or "기준" in c), None)
    foreign_col = next((c for c in kospi_df.columns if "외국인" in c), None)
    inst_col = next((c for c in kospi_df.columns if "기관" in c), None)

    if not (date_col and foreign_col and inst_col):
        log.warning(f"KRX 컬럼 인식 실패: {list(kospi_df.columns)}")
        return _empty("컬럼 매칭 실패")

    def _to_float(s):
        return pd.to_numeric(s.astype(str).str.replace(",", ""), errors="coerce").fillna(0)

    kospi_df = kospi_df.sort_values(date_col).tail(days)
    kosdaq_df = kosdaq_df.sort_values(date_col).tail(days) if not kosdaq_df.empty else kosdaq_df

    dates = kospi_df[date_col].astype(str).tolist()

    return KrxFlows(
        kospi_foreign_net=_to_float(kospi_df[foreign_col]).tolist(),
        kospi_inst_net=_to_float(kospi_df[inst_col]).tolist(),
        kosdaq_foreign_net=_to_float(kosdaq_df[foreign_col]).tolist() if foreign_col in kosdaq_df else [],
        kosdaq_inst_net=_to_float(kosdaq_df[inst_col]).tolist() if inst_col in kosdaq_df else [],
        dates=dates,
        by_ticker={},
        available=True,
    )


def fetch_ticker_foreign_ratio(tickers: list[str]) -> dict[str, dict]:
    """종목별 외국인 보유율 (최신).

    개별 종목 OTP 호출은 부하가 커서 보유 종목만 (한국 .KS/.KQ).
    """
    out: dict[str, dict] = {}
    today = datetime.now().strftime("%Y%m%d")
    for full in tickers:
        if not (full.endswith(".KS") or full.endswith(".KQ")):
            continue
        code = full.split(".")[0]
        params = {
            "bld": "dbms/MDC/STAT/standard/MDCSTAT03702",  # 외국인보유현황(개별)
            "searchType": "1",
            "mktId": "ALL",
            "trdDd": today,
            "isuCd": code,
            "strtDd": today,
            "endDd": today,
            "share": "1",
            "csvxls_isNo": "false",
        }
        df = _fetch_csv(params)
        if df.empty:
            continue
        # 컬럼: "일자", "종가", "지분율(%)", "한도소진율(%)" 형태
        ratio_col = next((c for c in df.columns if "지분율" in c), None)
        limit_col = next((c for c in df.columns if "소진" in c), None)
        if ratio_col:
            try:
                row = df.iloc[-1]
                out[full] = {
                    "지분율(%)": float(str(row[ratio_col]).replace(",", "")),
                    "한도소진율(%)": float(str(row[limit_col]).replace(",", "")) if limit_col else 0.0,
                }
            except Exception:
                pass
    return out
