"""중앙 설정 모듈. .env 로딩, 경로, 상수 정의."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

PORTFOLIO_CSV = ROOT / "portfolio.csv"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
CACHE_DIR = ROOT / "cache"
CACHE_DIR.mkdir(exist_ok=True)

GMAIL_SENDER = os.getenv("GMAIL_SENDER", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
REPORT_RECIPIENT = os.getenv("REPORT_RECIPIENT", GMAIL_SENDER)

FRED_API_KEY = os.getenv("FRED_API_KEY", "")
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")
FINNHUB_KEY = os.getenv("FINNHUB_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "").strip() or "gemini-2.0-flash"

# 한국투자증권 KIS OpenAPI (외국인·기관 수급 + VWAP)
KIS_APP_KEY = os.getenv("KIS_APP_KEY", "")
KIS_APP_SECRET = os.getenv("KIS_APP_SECRET", "")
KIS_ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO", "")

# 사이트 비밀번호 (StatiCrypt 암호화). 미설정 시 사이트가 평문으로 빌드됨 — 주의!
STATICRYPT_PASSWORD = os.getenv("STATICRYPT_PASSWORD", "")

# 입력 형식 — 기본은 확장 portfolio.csv (plain text).
# Excel 계속 쓰려면 .env에 USE_EXCEL_PORTFOLIO=true.
USE_EXCEL_PORTFOLIO = os.getenv("USE_EXCEL_PORTFOLIO", "false").lower() == "true"

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# 시장 벤치마크/지표 티커 (yfinance)
BENCHMARKS = {
    "KOSPI": "^KS11",
    "KOSDAQ": "^KQ11",
    "S&P500": "^GSPC",
    "NASDAQ": "^IXIC",
    "DOW": "^DJI",
    "닛케이225": "^N225",
    "상해종합": "000001.SS",
}

MACRO_TICKERS = {
    "미국채10Y": "^TNX",
    "미국채2Y": "^IRX",   # 13주 단기금리 (대체 지표)
    "미국채30Y": "^TYX",
    "VIX": "^VIX",
    "DXY 달러인덱스": "DX-Y.NYB",
    "WTI 원유": "CL=F",
    "금": "GC=F",
}

FX_TICKERS = {
    "USD/KRW": "KRW=X",
    "EUR/USD": "EURUSD=X",
    "USD/JPY": "JPY=X",
    "USD/CNY": "CNY=X",
}


def get_logger(name: str) -> logging.Logger:
    """파일+콘솔 양쪽 로깅."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(LOG_LEVEL)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    fh = logging.FileHandler(LOG_DIR / "advisor.log", encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger
