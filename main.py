"""포트폴리오 리스크 자문 — 사이트 빌드 진입점.

흐름
----
1. 포트폴리오 로드 (Excel 자동 파싱 또는 CSV)
2. 시장 데이터 수집 (벤치마크 / 매크로 / 환율 / 보유 종목)
3. 보조 데이터 수집 (KRX 수급, 이벤트 캘린더, 뉴스 + 감성분석)
4. 종목별 기술 분석 + 리스크 평가
5. 포트 전체 점수 산출
6. 정적 사이트 빌드 (dist/) + 아카이브 + 점수 추이
7. StatiCrypt 암호화 (STATICRYPT_PASSWORD 설정 시)
"""
from __future__ import annotations

import sys
import traceback

import pandas as pd

from analyzers.risk import HoldingRisk, evaluate_holding, evaluate_portfolio
from analyzers.technical import compute_tech
from collectors.ai_summarizer import generate_summaries
from collectors.calendar import fetch_upcoming_events
from collectors.fx import fetch_fx
from collectors.kis_api import fetch_market_investor_flows_kis, fetch_vwap_kis
from collectors.krx_flows import fetch_market_flows
from collectors.cot_flows import fetch_cot_sp500, fetch_etf_flows
from collectors.ecos import fetch_korea_macro
from collectors.eps_revision import fetch_eps_revisions
from collectors.market_breadth import compute_market_breadth
from collectors.sentiment import fetch_aaii_sentiment, fetch_put_call_ratio
from collectors.yen_carry import compute_yen_carry_risk
from collectors.macro import fetch_macro_snapshot
from collectors.news_sentiment import enrich_news_with_price, fetch_news_for_keywords
from collectors.non_tradeable_pricer import price_non_tradeable
from collectors.portfolio_loader import load_portfolio_from_csv, load_portfolio_from_excel
from collectors.prices import fetch_benchmarks, fetch_price_series, fetch_short_interest
from config import (
    ANTHROPIC_API_KEY,
    BENCHMARKS,
    GEMINI_API_KEY,
    PORTFOLIO_CSV,
    STATICRYPT_PASSWORD,
    USE_EXCEL_PORTFOLIO,
    get_logger,
)
from publisher.builder import BuildInputs, build_site
from publisher.encrypt import encrypt_dist, validate_password_strength

log = get_logger(__name__)


def _load_tradeable_others():
    """portfolio.csv (확장) 또는 Excel에서 추적/비추적 분리.

    반환: (tradeable_list_of_dicts, non_tradeable_holding_list)
    """
    if USE_EXCEL_PORTFOLIO:
        tradeable_h, others_h = load_portfolio_from_excel()
    else:
        tradeable_h, others_h = load_portfolio_from_csv()

    tradeable = [
        {
            "ticker": h.ticker,
            "name": h.name,
            "shares": h.quantity or 0,
            "avg_price": (h.value_krw / h.quantity) if (h.quantity and h.value_krw) else 0,
            "market": "KR" if (h.ticker or "").endswith((".KS", ".KQ")) else "US",
            "currency": h.currency or ("KRW" if (h.ticker or "").endswith((".KS", ".KQ")) else "USD"),
            "category": h.category,
            "init_value_krw": h.value_krw or 0,
            "return_pct_input": h.return_pct,
        }
        for h in tradeable_h
    ]
    return tradeable, others_h


def run() -> int:
    log.info("=" * 60)
    log.info("포트폴리오 리스크 자문 — 사이트 빌드 시작")
    log.info("=" * 60)

    # 비번 강도 검증 — 빌드 차단
    if STATICRYPT_PASSWORD:
        try:
            validate_password_strength(STATICRYPT_PASSWORD)
        except ValueError as exc:
            log.error(f"비밀번호 정책 위반: {exc}")
            return 3

    tradeable_input, non_tradeable = _load_tradeable_others()
    log.info(f"포트 로드: 추적 {len(tradeable_input)}건, 비추적 {len(non_tradeable)}건")

    log.info("매크로 / 환율 데이터 수집...")
    macro = fetch_macro_snapshot()
    fx = fetch_fx()
    macro.indicators.update(fx)

    log.info("벤치마크 지수 수집...")
    benchmarks = fetch_benchmarks(BENCHMARKS)

    usd_krw = fx["USD/KRW"].last_close if "USD/KRW" in fx else 1380.0
    log.info(f"USD/KRW = {usd_krw:.2f}")

    log.info("보유 종목 가격 데이터 수집...")
    holdings_value: list[dict] = []
    for h in tradeable_input:
        series = fetch_price_series(h["ticker"], h["name"])
        if series is None or not series.is_valid:
            log.warning(f"  {h['ticker']} 데이터 부족 — 스킵")
            continue

        last = series.last_close
        # Excel에서 온 항목은 shares·avg_price가 비어있을 수 있음 — 평가금액 직접 사용
        if h["shares"] > 0 and h["avg_price"] > 0:
            value_local = last * h["shares"]
            pnl_pct = (last - h["avg_price"]) / h["avg_price"] * 100
        else:
            value_local = h.get("init_value_krw", 0)
            pnl_pct = h.get("return_pct_input") or 0.0

        value_krw = value_local * (usd_krw if h["currency"] == "USD" else 1.0)

        holdings_value.append({
            "ticker": h["ticker"],
            "name": h["name"],
            "market": h["market"],
            "currency": h["currency"],
            "last_close": last,
            "daily_chg": series.pct_change,
            "value_local": value_local,
            "value_krw": value_krw,
            "pnl_pct": pnl_pct,
            "series": series,
        })

    if not holdings_value:
        log.error("유효한 추적 종목 없음 — 종료")
        return 1

    # 비추적 자산 일일 가격 책정 (확정수익·채권 ETF 대용·KOFIA 펀드)
    log.info("비추적 자산 가격 책정...")
    nt_stats = price_non_tradeable(non_tradeable)

    total_tradeable_krw = sum(h["value_krw"] for h in holdings_value)
    total_non_tradeable_krw = sum((h.value_krw or 0) for h in non_tradeable)
    log.info(f"추적 {total_tradeable_krw:,.0f}원 / 비추적 {total_non_tradeable_krw:,.0f}원")

    log.info("기술 지표 + 리스크 평가 (v2: 수급 구조 반영)...")
    # 벤치마크 daily close 추출 — 시장 상대강도 계산용
    kospi_close = benchmarks["KOSPI"].daily["Close"].dropna() if "KOSPI" in benchmarks else None
    sp500_close = benchmarks["S&P500"].daily["Close"].dropna() if "S&P500" in benchmarks else None

    # 섹터 ETF daily close 캐시 — 섹터 상대강도 계산용
    import pandas as pd
    sector_etf_cache: dict[str, pd.Series | None] = {}
    try:
        import csv as csv_mod
        from config import ROOT
        tmap_path = ROOT / "ticker_map.csv"
        if tmap_path.exists():
            with open(tmap_path, encoding="utf-8-sig") as f:
                for row in csv_mod.DictReader(f):
                    setf = (row.get("sector_etf") or "").strip()
                    if setf and setf not in sector_etf_cache:
                        sector_etf_cache[setf] = None  # placeholder
            for setf_ticker in list(sector_etf_cache.keys()):
                s = fetch_price_series(setf_ticker, setf_ticker)
                if s and not s.daily.empty:
                    sector_etf_cache[setf_ticker] = s.daily["Close"].dropna()
            log.info(f"  섹터 ETF {len([v for v in sector_etf_cache.values() if v is not None])}개 로드")
    except Exception as exc:
        log.debug(f"섹터 ETF 로드 실패: {exc}")

    # ticker → sector_etf 매핑
    ticker_sector_map: dict[str, str] = {}
    try:
        if tmap_path.exists():
            with open(tmap_path, encoding="utf-8-sig") as f:
                for row in csv_mod.DictReader(f):
                    tk = (row.get("ticker") or "").strip()
                    setf = (row.get("sector_etf") or "").strip()
                    if tk and setf:
                        ticker_sector_map[tk] = setf
    except Exception:
        pass

    holdings_risk: list[HoldingRisk] = []
    for h in holdings_value:
        weight = h["value_krw"] / total_tradeable_krw * 100
        market_close = kospi_close if h["market"] == "KR" else sp500_close
        tech = compute_tech(h["series"].daily, h["series"].weekly, market_close)

        # 섹터 상대강도 추가
        setf_ticker = ticker_sector_map.get(h["ticker"])
        if setf_ticker and setf_ticker in sector_etf_cache and sector_etf_cache[setf_ticker] is not None:
            from analyzers.technical import _relative_strength
            tech.rs_vs_sector_20d = _relative_strength(
                h["series"].daily["Close"].dropna(),
                sector_etf_cache[setf_ticker], 20
            )

        # 한국 종목에 VWAP 추가 (KIS 분봉)
        if h["market"] == "KR" and h["ticker"].endswith((".KS", ".KQ")):
            stock_code = h["ticker"].split(".")[0]
            vwap = fetch_vwap_kis(stock_code)
            if vwap:
                tech.vwap = vwap
                if tech.close > vwap:
                    tech.vwap_position = "above"
                else:
                    tech.vwap_position = "below"

        # 미국 종목에 숏 인터레스트 추가
        if h["market"] == "US":
            si = fetch_short_interest(h["ticker"])
            if si:
                tech.short_ratio = si.get("short_ratio")
                tech.short_pct_float = si.get("short_pct_float")

        holdings_risk.append(evaluate_holding(
            ticker=h["ticker"], name=h["name"], market=h["market"],
            weight_pct=weight, pnl_pct=h["pnl_pct"],
            tech=tech, macro=macro,
        ))

    risk = evaluate_portfolio(holdings_risk, macro, total_tradeable_krw)
    log.info(f"종합 점수 {risk.overall_score:.1f} → {risk.overall_action}")

    log.info("보조 데이터 수집 (KRX 수급)...")
    # KIS API 우선 → KRX OTP fallback
    from collectors.kis_api import InvestorFlows
    kis_flows = fetch_market_investor_flows_kis(days=7)
    if kis_flows.available:
        log.info(f"  KIS 외국인·기관 수급: {len(kis_flows.dates)}일")
        # KIS 결과를 기존 krx_flows 형식으로 변환 (template 호환)
        from collectors.krx_flows import KrxFlows
        krx = KrxFlows(
            kospi_foreign_net=kis_flows.foreign_net,
            kospi_inst_net=kis_flows.inst_net,
            kosdaq_foreign_net=[], kosdaq_inst_net=[],
            dates=kis_flows.dates, by_ticker={}, available=True,
        )
    else:
        krx = fetch_market_flows(days=7)
        log.info(f"  KIS 불가 → KRX OTP fallback, 가용: {krx.available}")

    log.info("경제 이벤트 캘린더...")
    events = fetch_upcoming_events(days_ahead=14)
    log.info(f"  이벤트 {len(events)}건")

    # v3 신규 — 시장 전망용 보조 지표
    log.info("시장 심리·구조 지표 수집 (Put/Call, AAII, 엔캐리, 시장 폭)...")
    put_call = fetch_put_call_ratio()
    aaii = fetch_aaii_sentiment()
    yen_carry = compute_yen_carry_risk(
        fx_indicators=fx, macro_indicators=macro.indicators,
        benchmarks=benchmarks, fred_data=macro.fred,
    )
    market_breadth = compute_market_breadth(benchmarks, holdings_risk)
    log.info(
        f"  Put/Call: {put_call.total_pc} ({put_call.interpretation or '데이터 없음'}) · "
        f"AAII spread: {aaii.bull_bear_spread} · "
        f"엔캐리 {yen_carry.score}/100 ({yen_carry.level}) · "
        f"시장 폭 US {market_breadth.us_breadth_chg_20d}"
    )

    # v3.1 신규 — EPS revision + COT + ETF flow
    log.info("EPS revision + COT + ETF flow 수집...")
    eps_tickers = [(h["ticker"], h["name"], h["market"]) for h in holdings_value]
    eps_revisions = fetch_eps_revisions(eps_tickers)
    cot = fetch_cot_sp500()
    etf_flows = fetch_etf_flows()
    log.info(
        f"  EPS revisions: {len(eps_revisions)}건 · "
        f"COT S&P500 net: {cot.sp500_mm_net} ({cot.interpretation}) · "
        f"ETF flows: {len(etf_flows)}건"
    )

    # 한국 거시 (ECOS)
    korea_macro = fetch_korea_macro()

    log.info("뉴스 수집...")
    news_keywords = ["코스피", "FOMC"] + [h.name for h in risk.holdings[:3]]
    news = fetch_news_for_keywords(news_keywords, per_kw=2)
    # 뉴스+가격 반응 결합
    holdings_chg_by_name = {h["name"]: h["daily_chg"] for h in holdings_value}
    enrich_news_with_price(news, holdings_chg_by_name)
    log.info(f"  헤드라인 {len(news)}건, 가격반응 매칭 {sum(1 for n in news if n.matched_ticker)}건")

    log.info(f"Gemini 섹션 요약 생성 (키 설정: {bool(GEMINI_API_KEY)})...")
    summaries = generate_summaries(
        risk, benchmarks, macro, holdings_value,
        yen_carry=yen_carry, market_breadth=market_breadth,
        put_call=put_call, aaii=aaii, krx_flows=krx,
        eps_revisions=eps_revisions, cot=cot, etf_flows=etf_flows,
        korea_macro=korea_macro,
    )

    log.info("사이트 빌드...")
    dist = build_site(
        BuildInputs(
            risk=risk,
            benchmarks=benchmarks,
            macro=macro,
            holdings_with_chg=holdings_value,
            non_tradeable=non_tradeable,
            total_tradeable_krw=total_tradeable_krw,
            total_non_tradeable_krw=total_non_tradeable_krw,
            krx=krx,
            events=events,
            news=news,
            has_claude=bool(ANTHROPIC_API_KEY),
            summaries=summaries,
        ),
        password=STATICRYPT_PASSWORD,
    )
    log.info(f"빌드 완료 → {dist}")

    if STATICRYPT_PASSWORD:
        log.info("StatiCrypt 암호화...")
        n = encrypt_dist(dist, STATICRYPT_PASSWORD)
        log.info(f"  {n}개 HTML 암호화 완료")
    else:
        log.warning("STATICRYPT_PASSWORD 미설정 — 사이트가 평문으로 빌드됨!")
        log.warning("프로덕션 배포 전 .env에 비번을 설정하세요.")

    log.info("완료")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except Exception as exc:
        log.error(f"치명적 오류: {exc}")
        log.error(traceback.format_exc())
        sys.exit(2)
