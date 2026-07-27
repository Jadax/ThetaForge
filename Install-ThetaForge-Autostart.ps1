<#
.SYNOPSIS
Installs a current-user Windows startup entry for the ThetaForge paper Bridge.
#>

$ErrorActionPreference = "Stop"
$startupKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$startupName = "ThetaForgePaperBridge"
$bridgeScript = Join-Path $PSScriptRoot "Start-ThetaForgeBridge.ps1"

if (-not (Test-Path -LiteralPath $bridgeScript)) {
    throw "ThetaForge Bridge launcher was not found. Keep this installer in the ThetaForge folder."
}

$command = "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$bridgeScript`""
New-Item -Path $startupKey -Force | Out-Null
Set-ItemProperty -Path $startupKey -Name $startupName -Value $command
Start-Process -FilePath "powershell.exe" -ArgumentList @(
    "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-File", $bridgeScript
) -WindowStyle Hidden

Write-Host "ThetaForge Paper Bridge will now start automatically when you sign in to Windows."
