"""EPS Revision — 컨센서스 추정치 변화.

미국: yfinance Ticker.earnings_estimate + info (trailingEps/forwardEps)
한국: 네이버 금융 컨센서스 페이지 크롤링 (기본 추정치 + 목표가)

trailing 대비 forward EPS 변화로 upward/downward revision 판단.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import requests
import yfinance as yf

from config import get_logger

log = get_logger(__name__)


@dataclass
class EPSRevision:
    ticker: str
    name: str
    trailing_eps: float | None = None
    forward_eps: float | None = None
    growth_pct: float | None = None    # (forward - trailing) / abs(trailing) * 100
    direction: str = ""                 # "upward" / "downward" / "flat"
    num_analysts: int | None = None
    target_price: float | None = None
    note: str = ""


def fetch_eps_revision_us(ticker: str, name: str = "") -> EPSRevision | None:
    """미국 종목 EPS revision. yfinance Ticker.info의 trailingEps vs forwardEps."""
    try:
        info = yf.Ticker(ticker).info
        trailing = info.get("trailingEps")
        forward = info.get("forwardEps")
        target = info.get("targetMeanPrice")
        num_analysts = info.get("numberOfAnalystOpinions")

        if trailing is None or forward is None:
            return None

        trailing = float(trailing)
        forward = float(forward)
        if trailing == 0:
            growth = None
            direction = "flat"
        else:
            growth = round((forward - trailing) / abs(trailing) * 100, 1)
            if growth >= 5:
                direction = "upward"
            elif growth <= -5:
                direction = "downward"
            else:
                direction = "flat"

        return EPSRevision(
            ticker=ticker, name=name or ticker,
            trailing_eps=round(trailing, 2),
            forward_eps=round(forward, 2),
            growth_pct=growth,
            direction=direction,
            num_analysts=int(num_analysts) if num_analysts else None,
            target_price=round(float(target), 2) if target else None,
        )
    except Exception as exc:
        log.debug(f"yfinance EPS revision 실패 ({ticker}): {exc}")
        return None


def fetch_eps_revision_kr(ticker: str, name: str = "") -> EPSRevision | None:
    """한국 종목 — 네이버 금융 페이지에서 PER/EPS 정보 크롤링.

    URL: https://finance.naver.com/item/main.naver?code={6자리}
    페이지 내부에 EPS·BPS·PER·예상EPS 등의 정보가 있음. 매우 단순한 파싱.
    """
    if not (ticker.endswith(".KS") or ticker.endswith(".KQ")):
        return None
    code = ticker.split(".")[0]
    url = f"https://finance.naver.com/item/main.naver?code={code}"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code != 200:
            return None
        text = r.text

        # 매우 단순한 정규식 추출 (구조 변경 시 갱신 필요)
        # "EPS(원)" 다음의 숫자, "추정 EPS(원)" 다음의 숫자
        eps_match = re.search(r"EPS\(원\).*?<em[^>]*>([\d,\-\.]+)</em>", text, re.DOTALL)
        est_eps_match = re.search(r"추정EPS\(원\).*?<em[^>]*>([\d,\-\.]+)</em>", text, re.DOTALL)

        def _to_float(s: str | None) -> float | None:
            if not s:
                return None
            try:
                return float(s.replace(",", ""))
            except ValueError:
                return None

        trailing = _to_float(eps_match.group(1)) if eps_match else None
        forward = _to_float(est_eps_match.group(1)) if est_eps_match else None

        if trailing is None and forward is None:
            return None

        if trailing and forward and trailing != 0:
            growth = round((forward - trailing) / abs(trailing) * 100, 1)
            if growth >= 5:
                direction = "upward"
            elif growth <= -5:
                direction = "downward"
            else:
                direction = "flat"
        else:
            growth = None
            direction = ""

        return EPSRevision(
            ticker=ticker, name=name or ticker,
            trailing_eps=trailing,
            forward_eps=forward,
            growth_pct=growth,
            direction=direction,
            note="네이버 금융 크롤링",
        )
    except Exception as exc:
        log.debug(f"네이버 EPS 크롤링 실패 ({ticker}): {exc}")
        return None


def fetch_eps_revisions(tickers: list[tuple[str, str, str]]) -> list[EPSRevision]:
    """tickers: list of (ticker, name, market)."""
    out: list[EPSRevision] = []
    for ticker, name, market in tickers:
        if market == "US":
            r = fetch_eps_revision_us(ticker, name)
        elif market == "KR":
            r = fetch_eps_revision_kr(ticker, name)
        else:
            r = None
        if r:
            out.append(r)
    log.info(f"EPS revision 수집: {len(out)}/{len(tickers)}")
    return out
