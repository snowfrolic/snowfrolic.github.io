"""저평가 종목 추천 — 글로벌 후보 풀에서 매수 기회 종목 발굴.

각 후보의 저평가 점수를 계산해 상위 N개를 추천 후보로 제공.
AI(Gemini)는 이 후보 + 시장 상황을 종합해 최종 3-5개 추천 + 이유 작성.

저평가 점수 (0-100, 높을수록 저평가):
- 52주 범위에서 낮은 위치 (40점)
- 200일선 대비 낮은 위치 (25점)
- RSI 낮음 (20점)
- 일별 RS(시장 대비) 약세 (15점)
"""
from __future__ import annotations

from dataclasses import dataclass

import yfinance as yf

from collectors.prices import fetch_price_series
from config import get_logger

log = get_logger(__name__)


# 후보 풀 — 글로벌 대표 종목·ETF·원자재 (보유 중 종목은 main에서 제외)
CANDIDATE_POOL: dict[str, dict] = {
    # 미국 대형주
    "AAPL":  {"name": "Apple", "category": "stock_us", "region": "US"},
    "MSFT":  {"name": "Microsoft", "category": "stock_us", "region": "US"},
    "GOOGL": {"name": "Alphabet", "category": "stock_us", "region": "US"},
    "AMZN":  {"name": "Amazon", "category": "stock_us", "region": "US"},
    "META":  {"name": "Meta", "category": "stock_us", "region": "US"},
    "NVDA":  {"name": "NVIDIA", "category": "stock_us", "region": "US"},
    "BRK-B": {"name": "Berkshire", "category": "stock_us", "region": "US"},
    "JPM":   {"name": "JPMorgan", "category": "stock_us", "region": "US"},
    "V":     {"name": "Visa", "category": "stock_us", "region": "US"},
    "JNJ":   {"name": "Johnson & Johnson", "category": "stock_us", "region": "US"},
    "WMT":   {"name": "Walmart", "category": "stock_us", "region": "US"},
    # 미국 ETF — 광범위
    "SPY":   {"name": "S&P 500", "category": "etf_us", "region": "US"},
    "QQQ":   {"name": "Nasdaq 100", "category": "etf_us", "region": "US"},
    "IWM":   {"name": "Russell 2000 (소형주)", "category": "etf_us", "region": "US"},
    "VTI":   {"name": "전미주식", "category": "etf_us", "region": "US"},
    "VEA":   {"name": "선진국 ex-US", "category": "etf_global", "region": "Global"},
    "VWO":   {"name": "신흥국", "category": "etf_em", "region": "EM"},
    "EFA":   {"name": "EAFE (유럽·일본)", "category": "etf_global", "region": "Global"},
    # 채권
    "TLT":   {"name": "미국 20Y+ 국채", "category": "bond", "region": "US"},
    "IEF":   {"name": "미국 7-10Y 국채", "category": "bond", "region": "US"},
    "HYG":   {"name": "미국 하이일드", "category": "bond", "region": "US"},
    "LQD":   {"name": "미국 IG 회사채", "category": "bond", "region": "US"},
    # 원자재
    "GLD":   {"name": "금", "category": "commodity", "region": "Global"},
    "SLV":   {"name": "은", "category": "commodity", "region": "Global"},
    "USO":   {"name": "원유 (WTI)", "category": "commodity", "region": "Global"},
    "DBC":   {"name": "원자재 종합", "category": "commodity", "region": "Global"},
    # 한국 대형주
    "207940.KS": {"name": "삼성바이오로직스", "category": "stock_kr", "region": "KR"},
    "373220.KS": {"name": "LG에너지솔루션", "category": "stock_kr", "region": "KR"},
    "035420.KS": {"name": "NAVER", "category": "stock_kr", "region": "KR"},
    "035720.KS": {"name": "카카오", "category": "stock_kr", "region": "KR"},
    "068270.KS": {"name": "셀트리온", "category": "stock_kr", "region": "KR"},
    "005490.KS": {"name": "POSCO홀딩스", "category": "stock_kr", "region": "KR"},
    "051910.KS": {"name": "LG화학", "category": "stock_kr", "region": "KR"},
    "012330.KS": {"name": "현대모비스", "category": "stock_kr", "region": "KR"},
    "066570.KS": {"name": "LG전자", "category": "stock_kr", "region": "KR"},
    "032830.KS": {"name": "삼성생명", "category": "stock_kr", "region": "KR"},
    # 한국 ETF
    "069500.KS": {"name": "KODEX 200", "category": "etf_kr", "region": "KR"},
    "229200.KS": {"name": "KODEX 코스닥150", "category": "etf_kr", "region": "KR"},
    "117460.KS": {"name": "KODEX 에너지", "category": "etf_kr", "region": "KR"},
    "139660.KS": {"name": "TIGER 200금융", "category": "etf_kr", "region": "KR"},
}


@dataclass
class UndervaluedCandidate:
    ticker: str
    name: str
    category: str
    region: str
    score: float                  # 0-100 저평가 점수
    last_close: float | None = None
    pct_52w: float | None = None  # 52주 범위 위치 (0=저점, 100=고점)
    vs_ma200_pct: float | None = None  # 200일선 대비 (%)
    rsi: float | None = None
    pe_forward: float | None = None
    market_chg_20d: float | None = None  # 시장(SPY/KOSPI) 대비 20일 RS
    notes: list[str] = None

    def __post_init__(self):
        if self.notes is None:
            self.notes = []


def _compute_score(daily, info: dict, market_close=None) -> tuple[float, dict]:
    """저평가 점수 + 진단 메트릭."""
    import numpy as np
    closes = daily["Close"].dropna()
    if len(closes) < 50:
        return 0.0, {}

    last = float(closes.iloc[-1])
    score = 0.0
    metrics = {"last_close": round(last, 2)}
    notes = []

    # 1) 52주 범위 위치 (40점)
    if len(closes) >= 252:
        high_52w = float(closes.tail(252).max())
        low_52w = float(closes.tail(252).min())
        if high_52w > low_52w:
            pct = (last - low_52w) / (high_52w - low_52w) * 100
            metrics["pct_52w"] = round(pct, 1)
            if pct <= 20:
                score += 40
                notes.append(f"52주 범위 {pct:.0f}% (저점 근접)")
            elif pct <= 35:
                score += 25
                notes.append(f"52주 범위 {pct:.0f}%")
            elif pct <= 50:
                score += 10

    # 2) 200일선 대비 (25점)
    if len(closes) >= 200:
        ma200 = float(closes.tail(200).mean())
        vs_ma = (last / ma200 - 1) * 100
        metrics["vs_ma200_pct"] = round(vs_ma, 1)
        if vs_ma <= -10:
            score += 25
            notes.append(f"200일선 -10%↓ ({vs_ma:+.1f}%)")
        elif vs_ma <= -5:
            score += 15
        elif vs_ma <= 0:
            score += 5

    # 3) RSI (20점)
    delta = closes.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = -delta.clip(upper=0).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] != 0 else np.nan
    if not np.isnan(rs):
        rsi = float(100 - 100 / (1 + rs))
        metrics["rsi"] = round(rsi, 1)
        if rsi <= 30:
            score += 20
            notes.append(f"RSI {rsi:.0f} 과매도")
        elif rsi <= 40:
            score += 12
            notes.append(f"RSI {rsi:.0f} 침체권")
        elif rsi <= 50:
            score += 5

    # 4) 시장 대비 20일 RS (15점)
    if market_close is not None and len(market_close) > 20 and len(closes) > 20:
        stock_ret = (float(closes.iloc[-1]) / float(closes.iloc[-21]) - 1) * 100
        market_ret = (float(market_close.iloc[-1]) / float(market_close.iloc[-21]) - 1) * 100
        rs_diff = stock_ret - market_ret
        metrics["market_chg_20d"] = round(rs_diff, 2)
        if rs_diff <= -10:
            score += 15
            notes.append(f"시장 대비 -10%↓ ({rs_diff:+.1f}%p)")
        elif rs_diff <= -5:
            score += 8

    # 5) Forward PE 보너스 (참고)
    fpe = info.get("forwardPE") if info else None
    if fpe and fpe > 0:
        metrics["pe_forward"] = round(float(fpe), 1)
        if fpe < 12:
            notes.append(f"Forward PE {fpe:.1f} (낮음)")

    metrics["notes"] = notes
    return score, metrics


def fetch_undervalued_candidates(
    held_tickers: set[str],
    spy_close=None,
    kospi_close=None,
    top_n: int = 10,
) -> list[UndervaluedCandidate]:
    """후보 풀에서 보유 종목 제외 후 저평가 점수 계산, 상위 N개 반환."""
    out: list[UndervaluedCandidate] = []
    n_checked = 0
    for ticker, meta in CANDIDATE_POOL.items():
        if ticker in held_tickers:
            continue
        try:
            ps = fetch_price_series(ticker, meta["name"])
            if ps is None or ps.daily.empty:
                continue
            n_checked += 1
            info = {}
            if meta["region"] == "US":
                try:
                    info = yf.Ticker(ticker).info
                except Exception:
                    info = {}
            market_close = kospi_close if meta["region"] == "KR" else spy_close
            score, m = _compute_score(ps.daily, info, market_close)
            if score >= 25:  # 최소 임계값
                out.append(UndervaluedCandidate(
                    ticker=ticker, name=meta["name"],
                    category=meta["category"], region=meta["region"],
                    score=round(score, 1),
                    last_close=m.get("last_close"),
                    pct_52w=m.get("pct_52w"),
                    vs_ma200_pct=m.get("vs_ma200_pct"),
                    rsi=m.get("rsi"),
                    pe_forward=m.get("pe_forward"),
                    market_chg_20d=m.get("market_chg_20d"),
                    notes=m.get("notes", []),
                ))
        except Exception as exc:
            log.debug(f"저평가 평가 실패 ({ticker}): {exc}")

    out.sort(key=lambda c: c.score, reverse=True)
    log.info(f"저평가 후보 평가: {n_checked}개 검토 → {len(out)}개 임계값 초과 → top {top_n} 반환")
    return out[:top_n]
