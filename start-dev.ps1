<#
ENCOMM SYSTEM WATCH — development launcher (Windows)

Starts the backend (FastAPI + collectors) and the frontend (Vite dev server),
both bound to localhost only. Only manages processes belonging to this
project; on exit (Ctrl+C / window close) it kills the exact process trees
it started. Requires: Python 3.11+ and Node.js 18+.

Usage:
    powershell -ExecutionPolicy Bypass -File .\start-dev.ps1
#>
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Join-Path $Root 'backend'
$FrontendDir = Join-Path $Root 'frontend'
$BackendPort = 8765
$FrontendPort = 5173

$Py = Join-Path $BackendDir '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) { $Py = 'python' }

# The backend venv must not inherit PYTHONPATH from the parent environment
# (it can shadow venv-installed packages).
$env:PYTHONPATH = ''

function Test-Port($Port) {
    $inUse = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return [bool]$inUse
}

if (Test-Port $BackendPort) { Write-Host "[FAIL] port $BackendPort already in use — is the backend already running?" -ForegroundColor Red; exit 1 }
if (Test-Port $FrontendPort) { Write-Host "[FAIL] port $FrontendPort already in use — is the frontend already running?" -ForegroundColor Red; exit 1 }

Write-Host "ENCOMM SYSTEM WATCH — starting (local only)" -ForegroundColor Cyan
Write-Host "  backend  http://127.0.0.1:$BackendPort"
Write-Host "  frontend http://localhost:$FrontendPort"

$Backend = Start-Process -FilePath $Py `
    -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$BackendPort") `
    -WorkingDirectory $BackendDir -WindowStyle Hidden -PassThru

$Frontend = Start-Process -FilePath 'npm.cmd' `
    -ArgumentList @('run', 'dev', '--', '--port', "$FrontendPort") `
    -WorkingDirectory $FrontendDir -WindowStyle Hidden -PassThru

Write-Host "  backend  PID $($Backend.Id)   frontend PID $($Frontend.Id)"
Write-Host "Press Ctrl+C to stop both..."

try {
    Wait-Process -Id $Backend.Id, $Frontend.Id -ErrorAction SilentlyContinue
}
finally {
    foreach ($proc in @($Backend, $Frontend)) {
        if (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue) {
            # taskkill /T kills the whole child tree (uvicorn reloader / node workers)
            & taskkill /PID $proc.Id /T /F 2>$null | Out-Null
        }
    }
    Write-Host "ENCOMM SYSTEM WATCH stopped." -ForegroundColor Cyan
}
