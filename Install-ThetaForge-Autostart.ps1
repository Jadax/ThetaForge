<#
.SYNOPSIS
Installs the current user's hidden Windows logon task for the ThetaForge paper Bridge.
#>

$ErrorActionPreference = "Stop"
$taskName = "ThetaForge Paper Bridge"
$bridgeScript = Join-Path $PSScriptRoot "Start-ThetaForgeBridge.ps1"

if (-not (Test-Path -LiteralPath $bridgeScript)) {
    throw "ThetaForge Bridge launcher was not found. Keep this installer in the ThetaForge folder."
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument (
    "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$bridgeScript`""
)
$trigger = New-ScheduledTaskTrigger -AtLogOn

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Description "Starts the local ThetaForge paper-only IBKR Bridge at Windows logon." -Force | Out-Null
Start-ScheduledTask -TaskName $taskName

Write-Host "ThetaForge Paper Bridge will now start automatically when you sign in to Windows."
