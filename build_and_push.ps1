# 로컬 빌드 + git push (Windows Task Scheduler 모드용)
# 사용법:
#   .\build_and_push.ps1
# 처음 실행 전 한 번:
#   git init; git remote add origin https://github.com/snowfrolic/snowfrolic.github.io.git
#   git branch -M main; git pull origin main --allow-unrelated-histories

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot
Set-Location $ScriptDir

if (Test-Path ".venv\Scripts\Activate.ps1") {
    & ".venv\Scripts\Activate.ps1"
}

Write-Host "[1/4] 사이트 빌드..." -ForegroundColor Cyan
python main.py
if ($LASTEXITCODE -ne 0) { Write-Error "빌드 실패"; exit $LASTEXITCODE }

Write-Host "[2/4] dist를 root에 동기화..." -ForegroundColor Cyan
# ⚠ history.json은 절대 복사하지 않음 — data/history.enc(암호화)만 commit
Copy-Item -Force "dist\index.html" ".\index.html"
Copy-Item -Force "dist\.nojekyll" ".\.nojekyll"
Copy-Item -Force "dist\robots.txt" ".\robots.txt"
if (Test-Path ".\archive") { Remove-Item -Recurse -Force ".\archive" }
if (Test-Path ".\static")  { Remove-Item -Recurse -Force ".\static" }
Copy-Item -Recurse "dist\archive" ".\archive"
Copy-Item -Recurse "dist\static"  ".\static"

Write-Host "[3/4] git commit + push..." -ForegroundColor Cyan
git add index.html archive/ static/ robots.txt .nojekyll data/history.enc
$status = git status --porcelain
if (-not $status) {
    Write-Host "  변경 없음 — 커밋 생략" -ForegroundColor Yellow
} else {
    $msg = "Daily build $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
    git commit -m $msg
    git push
}

Write-Host "[4/4] 완료 → https://snowfrolic.github.io" -ForegroundColor Green
