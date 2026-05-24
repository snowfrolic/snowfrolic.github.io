# GitHub Secrets 등록용 base64 인코딩 도구
# 사용법:
#   .\encode_secrets.ps1
#
# 결과는 logs\secrets_to_register.txt 에 저장됨.
# 파일 내용을 GitHub Settings -> Secrets and variables -> Actions 에 그대로 복사 붙여넣기.
#
# Excel 또는 매핑 파일이 바뀌면 이 스크립트를 다시 실행하고 해당 Secret을 업데이트.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path logs)) { New-Item -ItemType Directory -Path logs | Out-Null }
$OutFile = "logs\secrets_to_register.txt"

function EncodeFile($path, $secretName) {
    if (-not (Test-Path $path)) {
        return "[MISSING] $path  - skip"
    }
    $bytes = [System.IO.File]::ReadAllBytes($path)
    $b64 = [Convert]::ToBase64String($bytes)
    $size = $bytes.Length
    $b64Size = $b64.Length

    $header = "=" * 70
    $entry = @"
$header
SECRET NAME: $secretName
SOURCE FILE: $path
ORIGINAL: $size bytes  |  BASE64: $b64Size bytes
$header
$b64

"@
    return $entry
}

$output = ""
$output += "GitHub Secrets registration values`r`n"
$output += "Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')`r`n`r`n"
$output += "INSTRUCTIONS:`r`n"
$output += "  1. Go to https://github.com/snowfrolic/snowfrolic.github.io/settings/secrets/actions`r`n"
$output += "  2. For each section below, click 'New repository secret'`r`n"
$output += "  3. Use the SECRET NAME exactly as shown`r`n"
$output += "  4. Copy the base64 block (between separator lines) as the value`r`n"
$output += "  5. Secrets already required separately (no base64):`r`n"
$output += "       STATICRYPT_PASSWORD  - your site password`r`n"
$output += "       GEMINI_API_KEY       - Gemini API key (AIzaSy...)`r`n"
$output += "       FRED_API_KEY         - FRED API key`r`n`r`n"

$output += EncodeFile "포트폴리오 정리.xlsx" "PORTFOLIO_XLSX_B64"
$output += EncodeFile "ticker_map.csv"      "TICKER_MAP_B64"
$output += EncodeFile "yield_map.csv"       "YIELD_MAP_B64"
$output += EncodeFile "bond_etf_map.csv"    "BOND_ETF_MAP_B64"
$output += EncodeFile "fund_map.csv"        "FUND_MAP_B64"

[System.IO.File]::WriteAllText($OutFile, $output, [System.Text.Encoding]::UTF8)

Write-Host ""
Write-Host "[OK] Secrets encoded to: $OutFile" -ForegroundColor Green
Write-Host ""
Write-Host "File contents preview (first 20 lines):" -ForegroundColor Cyan
Get-Content $OutFile | Select-Object -First 20
Write-Host ""
Write-Host "Open the full file to copy values:" -ForegroundColor Cyan
Write-Host "   notepad $OutFile"
