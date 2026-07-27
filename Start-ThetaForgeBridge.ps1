<#
.SYNOPSIS
Starts the local ThetaForge paper Bridge without opening a console window.

.DESCRIPTION
Designed for the Windows logon task installed by Install-ThetaForge-Autostart.
It never starts or logs in to TWS; IBKR authentication remains in TWS itself.
#>

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot

function Test-ThetaForgeBridge {
    try {
        return (Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8002/health" -TimeoutSec 2).StatusCode -eq 200
    } catch {
        return $false
    }
}

function Find-PythonLauncher {
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) { return @{ File = $py.Source; Arguments = @("-3.12") } }

    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) { return @{ File = $python.Source; Arguments = @() } }

    throw "Python 3.12 was not found. Install Python 3.12, then start ThetaForge again."
}

if (-not (Test-ThetaForgeBridge)) {
    $python = Find-PythonLauncher
    $bridgeArguments = @($python.Arguments) + @(
        "-m", "uvicorn", "bridge.main:app", "--env-file", ".env",
        "--host", "127.0.0.1", "--port", "8002"
    )
    Start-Process -FilePath $python.File -ArgumentList $bridgeArguments -WorkingDirectory $projectRoot -WindowStyle Hidden
}
