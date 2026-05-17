"""HTML 리포트 생성 (Jinja2)."""
from __future__ import annotations

from datetime import datetime
from jinja2 import Template

from analyzers.risk import ACTION_LABELS, PortfolioRisk
from collectors.macro import MacroSnapshot
from collectors.prices import PriceSeries


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>{{ subject }}</title>
<style>
  body { font-family: 'Segoe UI', 'Apple SD Gothic Neo', sans-serif; background:#f5f6f8; margin:0; padding:24px; color:#212121; }
  .container { max-width: 900px; margin: 0 auto; }
  .hero { background: linear-gradient(135deg, {{ overall_color }} 0%, #263238 100%); color:#fff; padding:24px; border-radius:12px; }
  .hero h1 { margin:0 0 6px 0; font-size:22px; }
  .hero .score { font-size:48px; font-weight:700; line-height:1; }
  .hero .action { font-size:18px; margin-top:4px; opacity:0.95; }
  .hero .sub { font-size:13px; opacity:0.85; margin-top:8px; }
  .card { background:#fff; border-radius:10px; padding:18px; margin-top:16px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
  .card h2 { margin:0 0 12px 0; font-size:16px; border-left:4px solid #1976d2; padding-left:10px; }
  .warn { background:#fff3e0; border-left: 4px solid #f57c00; padding:10px 14px; border-radius:6px; margin-top:8px; font-size:14px; }
  .warn.long { background:#ffebee; border-color:#d32f2f; }
  table { width:100%; border-collapse: collapse; font-size:13px; }
  th, td { padding:8px 10px; text-align:left; border-bottom:1px solid #eceff1; }
  th { background:#fafafa; font-weight:600; color:#455a64; }
  td.num { text-align:right; font-variant-numeric: tabular-nums; }
  .chip { display:inline-block; padding:3px 8px; border-radius:10px; font-size:11px; font-weight:600; color:#fff; }
  .pos { color:#2e7d32; }
  .neg { color:#c62828; }
  .small { font-size:12px; color:#607d8b; }
  ul.signals { padding-left:18px; margin:4px 0; }
  ul.signals li { font-size:12px; color:#455a64; margin:2px 0; }
  .grid { display:grid; grid-template-columns: repeat(2, 1fr); gap:10px; }
  .stat { background:#fafafa; padding:10px; border-radius:6px; }
  .stat .label { font-size:11px; color:#607d8b; }
  .stat .value { font-size:18px; font-weight:600; }
  .footer { text-align:center; color:#90a4ae; font-size:11px; margin-top:18px; }
</style>
</head>
<body>
<div class="container">

  <div class="hero">
    <div class="small">{{ date_str }} · 포트폴리오 일일 리스크 리포트</div>
    <h1>종합 액션 — {{ overall_label }}</h1>
    <div class="score">{{ '%.1f'|format(risk.overall_score) }}<span style="font-size:18px; opacity:0.7;"> / 100</span></div>
    <div class="action">{{ overall_recommendation }}</div>
    <div class="sub">총 평가금액 · {{ total_value_str }}</div>
  </div>

  {% if risk.short_term_warning %}
  <div class="warn"><b>⚠️ 단기 경고</b> — {{ risk.short_term_warning }}</div>
  {% endif %}
  {% if risk.long_term_warning %}
  <div class="warn long"><b>🚨 장기 경고</b> — {{ risk.long_term_warning }}</div>
  {% endif %}

  <div class="card">
    <h2>오늘의 시장 (전일 종가)</h2>
    <table>
      <tr><th>지수</th><th class="num">종가</th><th class="num">전일대비</th></tr>
      {% for name, s in benchmarks.items() %}
      <tr>
        <td>{{ name }}</td>
        <td class="num">{{ '{:,.2f}'.format(s.last_close) }}</td>
        <td class="num {{ 'pos' if s.pct_change >= 0 else 'neg' }}">{{ '{:+.2f}%'.format(s.pct_change) }}</td>
      </tr>
      {% endfor %}
    </table>
  </div>

  <div class="card">
    <h2>금리 · 환율 · 변동성</h2>
    <table>
      <tr><th>항목</th><th class="num">값</th><th class="num">전일대비</th></tr>
      {% for name, s in fx_macro.items() %}
      <tr>
        <td>{{ name }}</td>
        <td class="num">{{ '{:,.4f}'.format(s.last_close) if s.last_close < 10 else '{:,.2f}'.format(s.last_close) }}</td>
        <td class="num {{ 'pos' if s.pct_change >= 0 else 'neg' }}">{{ '{:+.2f}%'.format(s.pct_change) }}</td>
      </tr>
      {% endfor %}
    </table>
    {% if yield_curve.spread_10y_3m is not none %}
    <div class="grid" style="margin-top:14px;">
      <div class="stat">
        <div class="label">미국채 10Y</div>
        <div class="value">{{ '%.2f%%'|format(yield_curve.us_10y or 0) }}</div>
      </div>
      <div class="stat">
        <div class="label">미국채 3M (^IRX)</div>
        <div class="value">{{ '%.2f%%'|format(yield_curve.us_3m or 0) }}</div>
      </div>
      <div class="stat" style="grid-column: 1 / span 2;">
        <div class="label">10Y - 3M 스프레드 {{ '(역전)' if yield_curve.inverted else '' }}</div>
        <div class="value {{ 'neg' if yield_curve.inverted else 'pos' }}">{{ '%+.2f' | format(yield_curve.spread_10y_3m) }}%p</div>
      </div>
    </div>
    {% endif %}

    {% if fred %}
    <h2 style="margin-top:18px;">미국 거시지표 (FRED 최신)</h2>
    <table>
      {% for label, val in fred.items() %}
      <tr><td>{{ label }}</td><td class="num">{{ '{:,.2f}'.format(val) }}</td></tr>
      {% endfor %}
    </table>
    {% endif %}
  </div>

  <div class="card">
    <h2>매크로 코멘트</h2>
    {% if risk.macro_notes %}
      <ul>
      {% for note in risk.macro_notes %}<li>{{ note }}</li>{% endfor %}
      </ul>
    {% else %}
      <div class="small">특이 신호 없음.</div>
    {% endif %}
  </div>

  <div class="card">
    <h2>보유 종목 리스크 · 액션</h2>
    <table>
      <tr>
        <th>종목</th>
        <th class="num">전일대비</th>
        <th class="num">비중</th>
        <th class="num">평가손익</th>
        <th class="num">단기</th>
        <th class="num">중기</th>
        <th class="num">장기</th>
        <th class="num">종합</th>
        <th>액션</th>
      </tr>
      {% for h in risk.holdings %}
      <tr>
        <td>
          <b>{{ h.name }}</b><br>
          <span class="small">{{ h.ticker }} · {{ h.market }}</span>
        </td>
        <td class="num {{ 'pos' if h.daily_chg >= 0 else 'neg' }}">{{ '{:+.2f}%'.format(h.daily_chg) }}</td>
        <td class="num">{{ '%.1f%%'|format(h.weight_pct) }}</td>
        <td class="num {{ 'pos' if h.pnl_pct >= 0 else 'neg' }}">{{ '{:+.1f}%'.format(h.pnl_pct) }}</td>
        <td class="num">{{ '%.0f'|format(h.short_score) }}</td>
        <td class="num">{{ '%.0f'|format(h.mid_score) }}</td>
        <td class="num">{{ '%.0f'|format(h.long_score) }}</td>
        <td class="num"><b>{{ '%.0f'|format(h.composite) }}</b></td>
        <td><span class="chip" style="background: {{ action_color[h.action] }};">{{ action_label[h.action] }}</span></td>
      </tr>
      {% if h.signals or h.warnings %}
      <tr><td colspan="9">
        <ul class="signals">
          {% for sig in h.signals %}<li>{{ sig }}</li>{% endfor %}
          {% for w in h.warnings %}<li style="color:#d32f2f;">⚠ {{ w }}</li>{% endfor %}
        </ul>
      </td></tr>
      {% endif %}
      {% endfor %}
    </table>
    <div class="small" style="margin-top:10px;">
      각 점수는 0(매수 우호) ~ 100(매도 우호). 종합 점수 = 단기 30% + 중기 40% + 장기 30%.
    </div>
  </div>

  <div class="footer">
    Generated at {{ generated_at }} · 본 리포트는 정보 제공용이며 투자 권유가 아닙니다.
  </div>

</div>
</body>
</html>
"""


def render_report(
    risk,
    benchmarks: dict[str, PriceSeries],
    macro: MacroSnapshot,
    holdings_with_chg: list[dict],
    total_value_str: str,
) -> tuple[str, str]:
    """리포트 HTML과 메일 subject를 반환."""
    # holdings에 daily_chg 주입
    chg_map = {h["ticker"]: h["daily_chg"] for h in holdings_with_chg}
    for h in risk.holdings:
        h.daily_chg = chg_map.get(h.ticker, 0.0)

    # 환율·매크로 묶음 (이미지가 너무 길지 않게)
    fx_macro_keys = ["USD/KRW", "USD/JPY", "EUR/USD", "VIX", "DXY 달러인덱스", "WTI 원유", "금"]
    fx_macro = {}
    for k in fx_macro_keys:
        if k in macro.indicators:
            fx_macro[k] = macro.indicators[k]

    overall_label, overall_recommendation, overall_color = ACTION_LABELS[risk.overall_action]
    action_color = {k: v[2] for k, v in ACTION_LABELS.items()}
    action_label = {k: v[1] for k, v in ACTION_LABELS.items()}

    now = datetime.now()
    subject = f"[포트 리스크 {risk.overall_action}] {overall_label} · 점수 {risk.overall_score:.0f}/100 ({now.strftime('%m/%d')})"

    html = Template(HTML_TEMPLATE).render(
        subject=subject,
        date_str=now.strftime("%Y년 %m월 %d일 (%a)"),
        generated_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        risk=risk,
        overall_label=overall_label,
        overall_recommendation=overall_recommendation,
        overall_color=overall_color,
        action_color=action_color,
        action_label=action_label,
        benchmarks=benchmarks,
        fx_macro=fx_macro,
        yield_curve=macro.yield_curve,
        fred=macro.fred,
        total_value_str=total_value_str,
    )
    return subject, html
