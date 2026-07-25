<#!
.SYNOPSIS
Starts the personal ThetaForge terminal with one double-click.

.DESCRIPTION
Starts the local, paper-only IBKR Bridge and the local dashboard in hidden
background processes, then opens the dashboard in the default browser.
TWS / IB Gateway is deliberately not logged into by this script: IBKR requires
an authenticated desktop session and credentials never belong in a launcher.
#>
param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$dashboardRoot = Join-Path $projectRoot "dashboard"

function Test-ThetaForgeService([string]$Url) {
    try {
        return (Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2).StatusCode -eq 200
    } catch {
        return $false
    }
}

function Find-PythonLauncher {
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) { return @{ File = $py.Source; Arguments = @("-3.12") } }

    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) { return @{ File = $python.Source; Arguments = @() } }

    throw "Python 3.12 was not found. Install Python 3.12, then run this launcher again."
}

if (-not (Test-ThetaForgeService "http://127.0.0.1:8002/health")) {
    $python = Find-PythonLauncher
    $bridgeArgs = @($python.Arguments) + @(
        "-m", "uvicorn", "bridge.main:app", "--env-file", ".env",
        "--host", "127.0.0.1", "--port", "8002"
    )
    Start-Process -FilePath $python.File -ArgumentList $bridgeArgs -WorkingDirectory $projectRoot -WindowStyle Hidden
}

if (-not (Test-ThetaForgeService "http://127.0.0.1:3000")) {
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npm) { throw "npm was not found. Install Node.js, then run this launcher again." }
    Start-Process -FilePath $npm.Source -ArgumentList @("run", "dev") -WorkingDirectory $dashboardRoot -WindowStyle Hidden
}

if (-not $NoBrowser) {
    Start-Sleep -Seconds 2
    Start-Process "http://localhost:3000"
}

Write-Host "ThetaForge is starting. Sign in to paper TWS / IB Gateway if it is not already running."
