# Daily build + git push (for Windows Task Scheduler)
# Output is appended to logs\build.log for diagnostics (esp. non-interactive runs)

$ErrorActionPreference = "Continue"
$ScriptDir = $PSScriptRoot
Set-Location $ScriptDir

if (-not (Test-Path logs)) { New-Item -ItemType Directory -Path logs | Out-Null }
$LogFile = Join-Path $ScriptDir "logs\build.log"

function Write-Log($Message) {
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$stamp $Message"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

Write-Log "===== BUILD START ====="

# venv
if (Test-Path ".venv\Scripts\Activate.ps1") {
    & ".venv\Scripts\Activate.ps1"
    Write-Log "[venv] activated"
}

# resolve git path (Task Scheduler PATH may be limited)
$gitExe = "git"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    $candidates = @("C:\Program Files\Git\cmd\git.exe", "C:\Program Files\Git\bin\git.exe")
    foreach ($c in $candidates) { if (Test-Path $c) { $gitExe = $c; break } }
}
Write-Log "[git] using $gitExe"

# [1] build
Write-Log "[1/4] python main.py"
$pyOut = & python main.py 2>&1
$pyExit = $LASTEXITCODE
foreach ($l in $pyOut) { Add-Content -Path $LogFile -Value $l -Encoding UTF8 }
if ($pyExit -ne 0) {
    Write-Log "[FAIL] build exit $pyExit"
    exit $pyExit
}
Write-Log "[OK] build done"

# [2] copy dist to root
Write-Log "[2/4] copy dist to root"
Copy-Item -Force "dist\index.html" ".\index.html"
Copy-Item -Force "dist\.nojekyll"  ".\.nojekyll"
Copy-Item -Force "dist\robots.txt" ".\robots.txt"
# Keep existing archive (past daily reports) + add today's. DO NOT rm -rf archive!
if (-not (Test-Path ".\archive")) { New-Item -ItemType Directory -Path ".\archive" | Out-Null }
if (-not (Test-Path ".\static"))  { New-Item -ItemType Directory -Path ".\static"  | Out-Null }
Copy-Item -Force "dist\archive\*" ".\archive\"
Copy-Item -Force "dist\static\*" ".\static\"
Write-Log "[OK] copy done"

# [3] git add / commit
Write-Log "[3/4] git add + commit"
$addOut = & $gitExe add index.html "archive/" "static/" robots.txt .nojekyll "data/history.enc" 2>&1
foreach ($l in $addOut) { Add-Content -Path $LogFile -Value "  [add] $l" -Encoding UTF8 }

$cached = & $gitExe diff --cached --name-only
if (-not $cached) {
    Write-Log "[SKIP] no staged changes"
} else {
    Write-Log "[stage] $($cached.Count) files"
    $msg = "Daily build " + (Get-Date -Format "yyyy-MM-dd HH:mm")
    $commitOut = & $gitExe commit -m $msg 2>&1
    foreach ($l in $commitOut) { Add-Content -Path $LogFile -Value "  [commit] $l" -Encoding UTF8 }
    if ($LASTEXITCODE -ne 0) {
        Write-Log "[FAIL] commit exit $LASTEXITCODE"
        exit $LASTEXITCODE
    }
    Write-Log "[OK] commit"
}

# [4] push (always try - covers accumulated local commits too)
Write-Log "[4/4] git push origin main"
$pushOut = & $gitExe push origin main 2>&1
$pushExit = $LASTEXITCODE
foreach ($l in $pushOut) { Add-Content -Path $LogFile -Value "  [push] $l" -Encoding UTF8 }
if ($pushExit -ne 0) {
    Write-Log "[FAIL] push exit $pushExit - check credential or network"
    Write-Log "[hint] try in interactive PowerShell: git push origin main"
    exit 0
}
Write-Log "[OK] push done"
Write-Log "===== BUILD COMPLETE ====="
