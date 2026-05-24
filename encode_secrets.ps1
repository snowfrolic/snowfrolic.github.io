# GitHub Secrets registration helper - base64 encoder
#
# WARNING: The output file contains ALL credentials in plain base64 form.
# base64 is NOT encryption - anyone with the file can decode the originals.
#
# SAFE HANDLING:
#   1. Run this script
#   2. Open the output file ONLY to copy values into GitHub Secrets page
#   3. NEVER paste base64 blocks anywhere else (chat, email, USB, cloud sync)
#   4. After registration, IMMEDIATELY run: .\cleanup_secrets.ps1
#
# Usage:
#   .\encode_secrets.ps1
# Cleanup after registration:
#   .\cleanup_secrets.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path logs)) { New-Item -ItemType Directory -Path logs | Out-Null }
$OutFile = "logs\secrets_to_register.txt"

# Refuse to run if output folder is inside OneDrive/iCloud/Dropbox (cloud sync risk)
$absRoot = (Get-Location).Path.ToLower()
foreach ($pattern in @("\onedrive\", "\dropbox\", "\icloud", "\google drive\")) {
    if ($absRoot.Contains($pattern)) {
        Write-Host ""
        Write-Host "[ABORT] Current directory looks like cloud sync folder:" -ForegroundColor Red
        Write-Host "  $((Get-Location).Path)" -ForegroundColor Red
        Write-Host "  Output base64 file would be auto-uploaded to cloud - DANGEROUS." -ForegroundColor Red
        Write-Host "  Move project to a non-synced folder (e.g. C:\Projects\) and rerun." -ForegroundColor Yellow
        exit 2
    }
}

function EncodeFile($path, $secretName) {
    if (-not (Test-Path $path)) {
        return "[MISSING] $path  - SKIP (Secret will not be set)`r`n`r`n"
    }
    $bytes = [System.IO.File]::ReadAllBytes($path)
    $b64 = [Convert]::ToBase64String($bytes)
    $size = $bytes.Length
    $b64Size = $b64.Length

    $header = "=" * 70
    return @"
$header
SECRET NAME: $secretName
SOURCE FILE: $path
ORIGINAL: $size bytes  |  BASE64: $b64Size bytes
$header
$b64

"@
}

$output = ""
$output += "GitHub Secrets registration values`r`n"
$output += "Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')`r`n`r`n"
$output += "##############################################################`r`n"
$output += "#  SECURITY WARNING                                          #`r`n"
$output += "#  Base64 is NOT encryption. Anyone who reads this file      #`r`n"
$output += "#  can recover the original portfolio + credentials.         #`r`n"
$output += "#                                                            #`r`n"
$output += "#  STEPS:                                                    #`r`n"
$output += "#  1. Use this file ONLY to copy values into GitHub Secrets  #`r`n"
$output += "#     page. NO other usage.                                  #`r`n"
$output += "#  2. After registration: run  .\cleanup_secrets.ps1        #`r`n"
$output += "#     to securely delete this file.                          #`r`n"
$output += "##############################################################`r`n`r`n"
$output += "URL: https://github.com/snowfrolic/snowfrolic.github.io/settings/secrets/actions`r`n`r`n"
$output += "Plain-text secrets to register separately (no base64 needed):`r`n"
$output += "  STATICRYPT_PASSWORD  - your site password (12 chars or more)`r`n"
$output += "  GEMINI_API_KEY       - Gemini API key (AIzaSy...)`r`n"
$output += "  FRED_API_KEY         - FRED API key (optional)`r`n"
$output += "  GEMINI_MODEL         - optional (default gemini-2.0-flash)`r`n`r`n"

$output += EncodeFile "포트폴리오 정리.xlsx" "PORTFOLIO_XLSX_B64"
$output += EncodeFile "ticker_map.csv"      "TICKER_MAP_B64"
$output += EncodeFile "yield_map.csv"       "YIELD_MAP_B64"
$output += EncodeFile "bond_etf_map.csv"    "BOND_ETF_MAP_B64"
$output += EncodeFile "fund_map.csv"        "FUND_MAP_B64"

[System.IO.File]::WriteAllText($OutFile, $output, [System.Text.Encoding]::UTF8)

# Set file as Hidden (small additional defense)
(Get-Item $OutFile).Attributes = 'Hidden'

Write-Host ""
Write-Host "==============================================================" -ForegroundColor Yellow
Write-Host "  Output: $OutFile" -ForegroundColor Yellow
Write-Host "  Contains ALL credentials in base64 (recoverable plain text)" -ForegroundColor Red
Write-Host "==============================================================" -ForegroundColor Yellow
Write-Host ""
Write-Host "NEXT STEPS:" -ForegroundColor Cyan
Write-Host "  1. notepad $OutFile        (copy values to GitHub Secrets page)"
Write-Host "  2. .\cleanup_secrets.ps1   (IMMEDIATELY after registration)"
Write-Host ""
Write-Host "DO NOT:" -ForegroundColor Red
Write-Host "  - paste base64 blocks into chat, email, USB, cloud drives"
Write-Host "  - leave this file on disk longer than necessary"
Write-Host "  - share screenshots showing the base64 content"
