# 포트폴리오 리스크 자문 서비스 (Phase 2)

매일 한 번 보유 포트폴리오의 리스크를 분석하고 비밀번호로 보호된 정적 사이트(GitHub Pages)에 게시합니다.

**👉 라이브 URL:** https://snowfrolic.github.io

---

## 무엇을 하나

1. **포트 자동 로드** — `포트폴리오 정리.xlsx` 자동 파싱 (또는 CSV)
   - 추적 가능 종목 (주식·ETF) → yfinance 일봉/주봉 분석
   - 비추적 자산 (펀드·연금·채권·현금) → 매크로 신호만 반영, 사이트에 별도 표시
2. **시장 데이터 수집**
   - 벤치마크: KOSPI, KOSDAQ, S&P500, NASDAQ, DOW, 닛케이, 상해
   - 금리: 미국채 10Y / 3M / 30Y · 수익률곡선 스프레드 · 역전 감지
   - 환율: USD/KRW, EUR/USD, USD/JPY, USD/CNY
   - 변동성: VIX · WTI 원유 · 금
   - 거시: FRED 미국 CPI · 비농업 고용 · 실업률 · 하이일드 스프레드 (FRED_API_KEY 필요)
   - KRX 외국인·기관 일별 순매수 (KRX API 차단 시 graceful skip)
3. **보조 데이터**
   - 향후 7일 경제 이벤트 캘린더 (FOMC · 한은 금통위 · CPI · 고용보고서)
   - 보유 종목·시장 뉴스 헤드라인 + Claude Haiku 감성분석 (ANTHROPIC_API_KEY 필요)
4. **리스크 평가** — 단기(1~2주) / 중기(1~6개월) / 장기(6개월+) 시계별 0~100 점수
5. **종합 액션** — L1 강한매수 / L2 매수우위 / L3 중립보유 / L4 일부 매도 / L5 위험회피
6. **AI 섹션 요약** — Gemini로 4개 섹션(종합 총평·시장·금리환율·보유종목) 2~3문장 자연어 요약
7. **사이트 빌드** — 최신 리포트 + 일자별 아카이브 + 30일 점수 추이 차트
8. **StatiCrypt 암호화** — 페이지 전체를 AES-256-GCM으로 암호화, 비번 입력 후 복호화

---

## 사용자 셋업 체크리스트

순서대로 완료하시면 다음 날 아침부터 사이트가 자동 갱신됩니다.

### 1. API 키 발급 (각 2~5분)

| 키 | 발급 URL | 필수 여부 |
|----|---------|----------|
| **STATICRYPT_PASSWORD** | 직접 결정 (12자 이상 권장) | **필수** — 사이트 보호 |
| **GEMINI_API_KEY** | https://aistudio.google.com/apikey | 권장 (무료, 결제수단 불필요. 섹션별 AI 요약) |
| **FRED_API_KEY** | https://fredaccount.stlouisfed.org/apikeys | 권장 (무료. 거시지표 풍부화) |
| **ANTHROPIC_API_KEY** | https://console.anthropic.com/settings/keys | 선택 (유료. 뉴스 헤드라인 감성분석) |

### 2. 로컬 환경 셋업

```powershell
# 작업 디렉토리에서
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 환경변수 파일 작성
Copy-Item .env.example .env
notepad .env   # 위 3개 키 + 사용자 비번 입력
```

### 3. 첫 빌드 검증

```powershell
python main.py
# dist/index.html이 생성됩니다.
# 브라우저로 dist/index.html을 열어 비밀번호 입력해 보세요.
```

### 4. GitHub Pages 배포 (한 번만)

#### 4a. snowfrolic.github.io repo 클론

```powershell
# 작업 디렉토리에서 git 초기화
git init
git remote add origin https://github.com/snowfrolic/snowfrolic.github.io.git
git branch -M main
git pull origin main --allow-unrelated-histories
```

> `.gitignore`가 포트폴리오 원본 (`포트폴리오 정리.xlsx`, `portfolio.csv`, `.env`, `dist/`)을 커밋에서 제외합니다.

#### 4b. 자동화 방식 선택

**옵션 A — Windows 작업 스케줄러 (PC 켜져 있을 때)**

```powershell
# 관리자 권한 PowerShell
.\setup_scheduler.ps1
```

매일 07:30에 PC에서 빌드 + GitHub push.

**옵션 B — GitHub Actions cron (PC 무관)**

`.github/workflows/daily.yml`이 이미 작성돼 있습니다. GitHub Secrets에 키 등록:

```
Repo Settings → Secrets and variables → Actions → New repository secret
```

| Secret 이름 | 값 |
|------------|-----|
| `STATICRYPT_PASSWORD` | 결정한 비밀번호 (12자 이상) |
| `GEMINI_API_KEY` | aistudio.google.com 발급 키 |
| `FRED_API_KEY` | 발급받은 키 (선택) |
| `ANTHROPIC_API_KEY` | 발급받은 키 (선택, 유료) |
| `PORTFOLIO_CSV_DATA` | `portfolio.csv` 파일 내용 그대로 (헤더 포함, 줄바꿈 보존) |

> Excel 자동 파싱은 사용자 PC에서만 가능. Cloud(Actions) 모드에서는 CSV 사용.

#### 4c. GitHub Pages 활성화

`snowfrolic.github.io` repo → Settings → Pages → Source: `Deploy from a branch` → `main` / `/ (root)` 선택.

### 5. 동작 확인

빌드 + push가 끝난 후 `https://snowfrolic.github.io` 접속 → 비밀번호 입력 → 리포트 표시.

---

## 디렉토리 구조

```
.
├── main.py                          # 빌드 진입점
├── config.py                        # 환경변수·상수
├── ticker_map.csv                   # 종목명 → yfinance 티커 매핑 (사용자 보강 가능)
├── 포트폴리오 정리.xlsx              # 원본 (.gitignore)
├── portfolio.csv                    # CSV 백업 (.gitignore)
├── .env / .env.example              # 환경변수
├── .github/workflows/daily.yml      # GitHub Actions cron
├── build_and_push.ps1               # 로컬 빌드+push 스크립트
├── setup_scheduler.ps1              # 작업 스케줄러 등록
│
├── collectors/                      # 데이터 수집
│   ├── portfolio_loader.py          # Excel 파서
│   ├── prices.py                    # yfinance 가격
│   ├── macro.py                     # 미국채·VIX·FRED
│   ├── fx.py                        # 환율
│   ├── krx_flows.py                 # KRX 외국인·기관 (best-effort)
│   ├── calendar.py                  # 경제 이벤트
│   └── news_sentiment.py            # 뉴스 + Claude 감성
│
├── analyzers/                       # 분석 엔진
│   ├── technical.py                 # MA·RSI·MACD·볼린저
│   └── risk.py                      # 단/중/장기 점수 + L1~L5
│
├── publisher/                       # 사이트 빌더
│   ├── builder.py                   # HTML 생성
│   ├── history.py                   # 점수 시계열
│   ├── encrypt.py                   # StatiCrypt
│   └── templates/report.html        # Jinja2 템플릿
│
├── reporting/                       # (이메일 발송 — 현재 비활성, 향후 노티에서 재활용)
│   ├── template.py
│   └── mailer.py
│
└── dist/                            # 빌드 산출물 (.gitignore)
    ├── index.html                   # 최신
    ├── archive/YYYY-MM-DD.html      # 일자별
    ├── history.json                 # 점수 시계열
    └── .nojekyll
```

---

## 리스크 레벨 정의

| 레벨 | 점수 (0–100) | 액션 |
|------|-------------|-----|
| **L1 강한 매수** | 0–20 | 분할 매수 추천 |
| **L2 매수 우위** | 20–40 | 보유 + 추가 매수 가능 |
| **L3 중립 보유** | 40–60 | 보유 |
| **L4 일부 차익실현** | 60–80 | 50% 매도 권장 |
| **L5 위험 회피** | 80–100 | 전량 매도 / 현금 비중 확대 |

별도 경고 신호:
- **단기 조정 예상** — RSI 과열·VIX 급등·이격도 과열
- **장기 침체 예상** — 수익률곡선 역전·고용지표 악화·DXY 급등

---

## 보안

- **사이트 암호화**: AES-256-GCM + PBKDF2 250,000회. 비번 12자 미만은 빌드 차단.
- **history 파일**: `data/history.enc`로 암호화 저장. 평문 `history.json`은 어디에도 생성하지 않음.
- **CSP/헤더**: `default-src 'self'`, `connect-src 'none'`, `frame-ancestors 'none'`, `referrer no-referrer`, `X-Content-Type-Options nosniff` 적용. 외부 도메인 통신 차단.
- **외부 의존 제거**: Chart.js는 `static/chart.umd.min.js`로 번들. CDN 침해 위험 없음.
- **비번 캐싱**: sessionStorage에 최대 **5분 TTL**. 탭 비가시화(`visibilitychange`) 시 즉시 삭제.
- **API 키**: Gemini는 헤더 인증(`x-goog-api-key`). URL/로그에 노출 안 됨.
- **`.gitignore`**: `.env*`, `*.xlsx`, `portfolio.csv`, `ticker_map.csv`, `dist/`, `data/*`(history.enc 제외).
- **`robots.txt` + `<meta noindex>`** 이중 차단.
- **외부 링크**: `rel="noopener noreferrer"`. tabnabbing·referer leak 방지.

비밀번호를 잊으면 사이트는 영구 잠김. `.env`에서 비번 바꾼 뒤 재빌드하면 다음 빌드부터 새 비번으로 작동(과거 archive는 옛 비번 그대로 잠긴 채 유지).

---

## 트러블슈팅

| 증상 | 원인 / 해결 |
|------|------------|
| Excel 파싱 시 일부 종목 누락 | `logs/unmatched.csv` 확인 → `ticker_map.csv`에 매핑 추가 |
| KRX 수급 데이터 비어 있음 | KRX가 API endpoint를 자주 변경. graceful skip 처리됨 |
| FRED 지표 빈 값 | `FRED_API_KEY` 미설정 |
| 뉴스 감성 모두 neutral | `ANTHROPIC_API_KEY` 미설정 |
| 사이트가 평문 노출 | `STATICRYPT_PASSWORD` 미설정 — 절대 안 됨 |
| Actions 실행 안 됨 | repo Secrets에 4개 키 모두 등록됐는지 확인 |
| Gmail 비밀번호 워닝 | 현재 이메일 발송은 비활성 (reporting/ 모듈 보존 중) |

---

## 향후 확장 (Phase 3 후보)

- 한국투자증권 KIS OpenAPI 실계좌 자동 동기화
- 노티 (이메일·텔레그램·Slack) — 점수가 임계값 돌파 시
- 증권사 리서치센터 리포트 제목 크롤링
- 백테스트 모드 — 과거 시점의 점수가 실제 성과를 얼마나 잘 예측했나
