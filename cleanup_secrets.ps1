# Securely delete secrets_to_register.txt
# Overwrites with random data 3 times before delete - resists casual undelete tools.
#
# Run this IMMEDIATELY after copying values to GitHub Secrets page.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$target = "logs\secrets_to_register.txt"
if (-not (Test-Path $target)) {
    Write-Host "[OK] $target not found - nothing to clean." -ForegroundColor Green
    exit 0
}

$item = Get-Item $target -Force
$size = $item.Length
Write-Host "Target: $target ($size bytes)" -ForegroundColor Cyan

# Overwrite with random bytes (3 passes)
try {
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    for ($pass = 1; $pass -le 3; $pass++) {
        $fs = [System.IO.File]::OpenWrite($item.FullName)
        try {
            $buffer = New-Object byte[] 4096
            $written = 0
            while ($written -lt $size) {
                $rng.GetBytes($buffer)
                $toWrite = [Math]::Min(4096, $size - $written)
                $fs.Write($buffer, 0, $toWrite)
                $written += $toWrite
            }
            $fs.Flush()
        } finally {
            $fs.Dispose()
        }
        Write-Host "  pass $pass/3 overwrite done" -ForegroundColor Gray
    }
    $rng.Dispose()

    # Final delete
    Remove-Item $target -Force
    Write-Host "[OK] securely deleted." -ForegroundColor Green

    # Verify
    if (Test-Path $target) {
        Write-Host "[WARN] file still exists - check manually." -ForegroundColor Yellow
        exit 1
    }
} catch {
    Write-Host "[ERROR] $_" -ForegroundColor Red
    exit 2
}

Write-Host ""
Write-Host "Reminder:" -ForegroundColor Cyan
Write-Host "  - File system shadow copies / Recycle Bin may still hold remnants."
Write-Host "  - For deeper assurance, restart PC after this cleanup."
