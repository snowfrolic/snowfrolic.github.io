"""정적 사이트 빌더 — dist/ 디렉토리에 HTML 생성.

산출물:
  dist/index.html              ← 최신 리포트
  dist/archive/YYYY-MM-DD.html ← 일자별 아카이브
  dist/history.json            ← 점수 시계열
  dist/.nojekyll               ← GitHub Pages가 _ 시작 파일 무시 안 하도록
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from analyzers.risk import ACTION_LABELS
from config import ROOT, get_logger
from publisher.history import get_archive_links, update_history

log = get_logger(__name__)

DIST_DIR = ROOT / "dist"
ARCHIVE_DIR = DIST_DIR / "archive"
STATIC_DIR = DIST_DIR / "static"
ASSETS_DIR = ROOT / "assets"
DATA_DIR = ROOT / "data"
HISTORY_PATH = DATA_DIR / "history.enc"   # 암호화 파일 (dist 밖, 또한 root에서도 안전)
TEMPLATE_DIR = ROOT / "publisher" / "templates"


@dataclass
class BuildInputs:
    risk: Any
    benchmarks: dict
    macro: Any
    holdings_with_chg: list[dict]
    non_tradeable: list   # publisher 직접 사용용 (펀드·채권·현금·연금)
    total_tradeable_krw: float
    total_non_tradeable_krw: float
    krx: Any | None = None
    events: list | None = None
    news: list | None = None
    has_claude: bool = False
    summaries: Any | None = None   # SectionSummaries


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=False,
        lstrip_blocks=False,
    )


def _format_currency(value: float) -> str:
    if value >= 1e8:
        return f"₩{value/1e8:.2f}억"
    if value >= 1e4:
        return f"₩{value/1e4:.0f}만"
    return f"₩{value:,.0f}"


def _render(inputs: BuildInputs, history: list[dict], archive_links: list[dict], is_archive: bool = False) -> tuple[str, str]:
    risk = inputs.risk
    # holdings에 daily_chg 주입
    chg_map = {h["ticker"]: h["daily_chg"] for h in inputs.holdings_with_chg}
    for h in risk.holdings:
        h.daily_chg = chg_map.get(h.ticker, 0.0)

    fx_macro_keys = ["USD/KRW", "USD/JPY", "EUR/USD", "VIX", "DXY 달러인덱스", "WTI 원유", "금"]
    fx_macro = {k: inputs.macro.indicators[k] for k in fx_macro_keys if k in inputs.macro.indicators}

    overall_label, overall_recommendation, overall_color = ACTION_LABELS[risk.overall_action]
    action_color = {k: v[2] for k, v in ACTION_LABELS.items()}
    action_label = {k: v[1] for k, v in ACTION_LABELS.items()}

    from datetime import timezone, timedelta
    KST = timezone(timedelta(hours=9))
    now = datetime.now(KST)
    total_all = inputs.total_tradeable_krw + inputs.total_non_tradeable_krw

    subject = f"[포트 리스크 {risk.overall_action}] {overall_label} · {risk.overall_score:.0f}/100 ({now.strftime('%m/%d')})"

    chart_js_src = "../static/chart.umd.min.js" if is_archive else "static/chart.umd.min.js"

    # 데이터 최신 일자 추출 — yfinance가 한국·미국 시장 데이터 동기화에 지연이 있어
    # 사용자에게 실제 어느 일자 종가인지 명시 (오해 방지)
    def _last_date(series):
        try:
            closes = series.daily["Close"].dropna()
            if not closes.empty:
                return closes.index[-1].strftime("%Y-%m-%d")
        except Exception:
            pass
        return None

    bench_dates = {name: _last_date(s) for name, s in inputs.benchmarks.items()}
    # KR과 US 각각 가장 최근 일자
    kr_keys = ["KOSPI", "KOSDAQ", "닛케이225", "상해종합"]
    us_keys = ["S&P500", "NASDAQ", "DOW"]
    kr_date = next((bench_dates[k] for k in kr_keys if bench_dates.get(k)), None)
    us_date = next((bench_dates[k] for k in us_keys if bench_dates.get(k)), None)

    html = _env().get_template("report.html").render(
        subject=subject,
        date_str=now.strftime("%Y년 %m월 %d일 (%a)"),
        generated_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        risk=risk,
        overall_label=overall_label,
        overall_recommendation=overall_recommendation,
        overall_color=overall_color,
        action_color=action_color,
        action_label=action_label,
        action_legend=ACTION_LABELS,
        benchmarks=inputs.benchmarks,
        fx_macro=fx_macro,
        yield_curve=inputs.macro.yield_curve,
        fred=inputs.macro.fred,
        total_value_str=_format_currency(total_all),
        non_tradeable=inputs.non_tradeable,
        non_tradeable_total_str=_format_currency(inputs.total_non_tradeable_krw),
        krx=inputs.krx,
        events=inputs.events or [],
        news=inputs.news or [],
        has_claude=inputs.has_claude,
        summaries=inputs.summaries,
        history=history,
        archive_links=archive_links,
        is_archive=is_archive,
        relative_archive_prefix="../" if is_archive else "",
        chart_js_src=chart_js_src,
        bench_dates=bench_dates,
        kr_data_date=kr_date,
        us_data_date=us_date,
    )
    return subject, html


def _copy_static_assets() -> None:
    """assets/ 의 정적 자원을 dist/static/ 으로 복사 — CDN 의존 제거."""
    import shutil
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    chart_src = ASSETS_DIR / "chart.umd.min.js"
    if chart_src.exists():
        shutil.copy2(chart_src, STATIC_DIR / "chart.umd.min.js")


def _write_robots_txt() -> None:
    """검색엔진 차단 — meta noindex와 별개로 robots.txt 도 명시."""
    (DIST_DIR / "robots.txt").write_text(
        "User-agent: *\nDisallow: /\n", encoding="utf-8"
    )


def build_site(inputs: BuildInputs, password: str = "") -> Path:
    """사이트 빌드. history 암호화에 STATICRYPT_PASSWORD 필요."""
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DIST_DIR / ".nojekyll").write_text("", encoding="utf-8")
    _copy_static_assets()
    _write_robots_txt()

    if not password:
        log.warning("password 없이 build_site 호출 — history 갱신 스킵")
        history: list[dict] = []
    else:
        history = update_history(
            HISTORY_PATH,
            inputs.risk.overall_score,
            inputs.risk.overall_action,
            inputs.total_tradeable_krw + inputs.total_non_tradeable_krw,
            password=password,
        )
    archive_links = get_archive_links(DIST_DIR, limit=30)

    # 최신
    _, html = _render(inputs, history, archive_links, is_archive=False)
    (DIST_DIR / "index.html").write_text(html, encoding="utf-8")
    log.info(f"dist/index.html 작성 ({len(html):,} bytes)")

    # 일자별 아카이브
    today = datetime.now().strftime("%Y-%m-%d")
    archive_path = ARCHIVE_DIR / f"{today}.html"
    # 아카이브 사이드바도 같은 링크 (자기 자신 포함)
    archive_links_for_archive = get_archive_links(DIST_DIR, limit=30)
    _, html_arch = _render(inputs, history, archive_links_for_archive, is_archive=True)
    archive_path.write_text(html_arch, encoding="utf-8")
    log.info(f"dist/archive/{today}.html 작성")

    return DIST_DIR
