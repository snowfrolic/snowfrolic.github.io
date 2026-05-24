# GitHub Secrets registration guide
#
# All portfolio data is now plain text (portfolio.csv + 4 mapping csv files).
# No base64 encoding needed - just open each csv in notepad and copy the contents.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Refuse to run if folder is under cloud sync (defense against accidental upload)
$absRoot = (Get-Location).Path.ToLower()
foreach ($pattern in @("\onedrive\", "\dropbox\", "\icloud", "\google drive\")) {
    if ($absRoot.Contains($pattern)) {
        Write-Host ""
        Write-Host "[WARN] Current directory looks like a cloud sync folder:" -ForegroundColor Yellow
        Write-Host "  $((Get-Location).Path)" -ForegroundColor Yellow
        Write-Host "  Be careful not to save secret values into the same folder." -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "===============================================================" -ForegroundColor Cyan
Write-Host "  GitHub Secrets registration - paste plain-text values"        -ForegroundColor Cyan
Write-Host "===============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "URL: https://github.com/snowfrolic/snowfrolic.github.io/settings/secrets/actions"
Write-Host ""
Write-Host "Click 'New repository secret', use Name exactly as shown,"
Write-Host "Value = file contents (open in notepad, Ctrl+A, Ctrl+C)."
Write-Host ""

$secrets = @(
    @{ Name="STATICRYPT_PASSWORD"; Source=".env (STATICRYPT_PASSWORD)"; Type="plain" },
    @{ Name="GEMINI_API_KEY";      Source=".env (GEMINI_API_KEY)";      Type="plain" },
    @{ Name="FRED_API_KEY";        Source=".env (FRED_API_KEY)";        Type="plain" },
    @{ Name="PORTFOLIO_CSV_DATA";  Source="portfolio.csv";              Type="file"  },
    @{ Name="TICKER_MAP_DATA";     Source="ticker_map.csv";             Type="file"  },
    @{ Name="YIELD_MAP_DATA";      Source="yield_map.csv";              Type="file"  },
    @{ Name="BOND_ETF_MAP_DATA";   Source="bond_etf_map.csv";           Type="file"  },
    @{ Name="FUND_MAP_DATA";       Source="fund_map.csv";               Type="file"  }
)

$idx = 0
foreach ($s in $secrets) {
    $idx++
    $sizeNote = ""
    if ($s.Type -eq "file") {
        if (Test-Path $s.Source) {
            $sizeNote = " ($(((Get-Item $s.Source).Length)) bytes)"
        } else {
            $sizeNote = " [MISSING]"
        }
    }
    Write-Host ("{0,2}. Name:   {1}" -f $idx, $s.Name) -ForegroundColor Green
    Write-Host ("    Source: {0}{1}" -f $s.Source, $sizeNote)
    Write-Host ""
}

Write-Host "===============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Quick copy commands (open file in notepad, then Ctrl+A and Ctrl+C):" -ForegroundColor Cyan
Write-Host "  notepad portfolio.csv"
Write-Host "  notepad ticker_map.csv"
Write-Host "  notepad yield_map.csv"
Write-Host "  notepad bond_etf_map.csv"
Write-Host "  notepad fund_map.csv"
Write-Host ""
Write-Host "After registration, verify on the Secrets page that all 8 names are listed." -ForegroundColor Cyan
