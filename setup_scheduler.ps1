# Windows 작업 스케줄러에 매일 07:30 KST 빌드+푸시 작업 등록
# 사용법 (관리자 권한 PowerShell):
#   .\setup_scheduler.ps1
# 제거:
#   Unregister-ScheduledTask -TaskName "PortfolioRiskAdvisor" -Confirm:$false

$ErrorActionPreference = "Stop"
$TaskName = "PortfolioRiskAdvisor"
$ScriptDir = $PSScriptRoot
$Script = Join-Path $ScriptDir "build_and_push.ps1"

if (-not (Test-Path $Script)) {
    Write-Error "build_and_push.ps1을 찾을 수 없습니다: $Script"
    exit 1
}

# 기존 작업 제거
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "기존 작업 제거..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Script`"" `
    -WorkingDirectory $ScriptDir

$Trigger = New-ScheduledTaskTrigger -Daily -At "7:30 AM"

# 잠자기 상태이면 깨워서 실행하는 옵션은 Settings의 -WakeToRun 으로 처리
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -WakeToRun `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

$Principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "매일 07:30 포트폴리오 리스크 사이트 빌드 + GitHub Pages push" | Out-Null

Write-Host ""
Write-Host "[OK] 작업 등록 완료: $TaskName" -ForegroundColor Green
Write-Host "     실행 시각: 매일 07:30 (잠자기 상태면 깨워서 실행)"
Write-Host "     스크립트: $Script"
Write-Host ""
Write-Host "지금 한 번 실행:" -ForegroundColor Cyan
Write-Host "   Start-ScheduledTask -TaskName $TaskName"
Write-Host ""
Write-Host "다음 실행 시각 확인:" -ForegroundColor Cyan
Write-Host "   Get-ScheduledTaskInfo -TaskName $TaskName"
