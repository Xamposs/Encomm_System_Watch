<#
ENCOMM SYSTEM WATCH - canonical production launcher (Windows 11)

Starts the release backend (FastAPI + collectors + real ETW when elevated)
serving the PRODUCTION frontend build from frontend\dist. Binds 127.0.0.1
ONLY. Never auto-elevates, never stops ETW sessions, never kills unrelated
processes, and does NOT start the Vite dev server.

Usage:
    .\Start-SystemWatch.ps1              normal start
    .\Start-SystemWatch.ps1 -NoBrowser   start without opening the UI

First time on a fresh clone?  Run .\Setup-SystemWatch.ps1 first.
For full TIER2 ETW (per-edge bytes), run this from an ADMINISTRATOR
PowerShell.  Non-admin is fully supported: the app truthfully reports
TIER0 (socket lifecycle + adapter totals).
#>
[CmdletBinding()]
param(
    [switch]$NoBrowser,
    [int]$Port = 8765
)

$ErrorActionPreference = 'Stop'
$Version = '1.0.0'

# --- project layout (all path operations are space-safe via Join-Path) ----
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Join-Path $Root 'backend'
$FrontendDir = Join-Path $Root 'frontend'
$DistDir = Join-Path $FrontendDir 'dist'
$Py = Join-Path $BackendDir '.venv\Scripts\python.exe'

# Read the canonical product version from package.json when possible.
if (Test-Path (Join-Path $FrontendDir 'package.json')) {
    try {
        $pkg = Get-Content (Join-Path $FrontendDir 'package.json') -Raw | ConvertFrom-Json
        if ($pkg.version) { $Version = $pkg.version }
    } catch { }
}

# --- 1. existing instance / port check (never kill anything) ---------------
$existingPid = $null
try {
    $listeners = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($listeners) { $existingPid = $listeners[0].OwningProcess }
} catch { }

if ($existingPid) {
    $healthy = $false
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 3
        $healthy = ($r.status -eq 'ok')
    } catch { }
    if ($healthy) {
        Write-Host ''
        Write-Host "ENCOMM SYSTEM WATCH $Version - already running (PID $existingPid)" -ForegroundColor Yellow
        Write-Host '--------------------------------'
        Write-Host "Backend:   http://127.0.0.1:$Port"
        Write-Host "UI:        http://127.0.0.1:$Port"
        Write-Host 'No duplicate backend was started.'
        Write-Host ''
    } else {
        Write-Host '[FAIL] 127.0.0.1:'$Port' is already in use by another application (PID '$existingPid').' -ForegroundColor Red
        Write-Host 'SYSTEM WATCH does not kill foreign processes.  Free the port or use -Port <other>.'
        exit 1
    }
    exit 0
}

# --- 2. stale owned ETW session warning (informational only, never stops) ---
try {
    $sessions = & logman query -ets 2>$null | Out-String
    if ($sessions -match 'esw-telemetry') {
        Write-Host ''
        Write-Host 'WARNING: a pre-existing `esw-telemetry` ETW session was detected.' -ForegroundColor Yellow
        Write-Host '  If it belongs to a CRASHED previous SYSTEM WATCH instance, stop ONLY that'
        Write-Host '  session before starting a fresh TIER2 run (elevated):'
        Write-Host '      logman stop esw-telemetry -ets'
        Write-Host '  SYSTEM WATCH will not stop it automatically.'
        Write-Host ''
    }
} catch { }

# --- 3. prerequisite checks -------------------------------------------------
if (-not (Test-Path $Py)) {
    Write-Host '[FAIL] Backend Python not found: backend\.venv was not created.' -ForegroundColor Red
    Write-Host '       Run Setup-SystemWatch.ps1 first (creates the venv and installs dependencies), or:'
    Write-Host '       cd backend ; python -m venv .venv ; .\.venv\Scripts\python.exe -m pip install -r requirements.txt'
    exit 1
}
if (-not (Test-Path (Join-Path $DistDir 'index.html'))) {
    Write-Host "[FAIL] Frontend production build not found here build (frontend\dist is missing)." -ForegroundColor Red
    Write-Host '       Run Setup-SystemWatch.ps1 first (runs npm ci + npm run build), or:'
    Write-Host '       cd frontend ; npm ci ; npm run build'
    exit 1
}

# --- 4. elevation / telemetry expectations -------------------------------------
$elevated = $false
try {
    $wid = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $pr  = New-Object System.Security.Principal.WindowsPrincipal($wid)
    $elevated = $pr.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
} catch { }

# --- 5. engine env hygiene (venv must not inherit a leaking PYTHONPATH) ---------
$env:PYTHONPATH = ''
if ($env:ESW_HOST -and $env:ESW_HOST -ne '127.0.0.1') {
    Write-Host "[WARN] ESW_HOST=$($env:ESW_HOST) is set; the launcher always binds 127.0.0.1." -ForegroundColor Yellow
}
if ($env:ESW_PORT -and [int]$env:ESW_PORT -ne $Port) {
    Write-Host "[WARN] ESW_PORT=$($env:ESW_PORT) conflicts with -Port; the launcher passes its own --port." -ForegroundColor Yellow
}
if ($env:ESW_DEMO_MODE) { Write-Host '[WARN] ESW_DEMO_MODE is set; the app will start in synthetic DEMO mode.' -ForegroundColor Yellow }
if ($env:ESW_AI_TELEMETRY_FIXTURE) { Write-Host '[WARN] ESW_AI_TELEMETRY_FIXTURE is set; AI telemetry starts in TEST/FIXTURE mode.' -ForegroundColor Yellow }

Write-Host ''
Write-Host "ENCOMM SYSTEM WATCH $Version" -ForegroundColor Cyan
Write-Host ("-" * 34)
Write-Host "Backend:   http://127.0.0.1:$Port   (localhost only)"
if ($elevated) { Write-Host 'Administrator: YES' } else { Write-Host 'Administrator: NO' -ForegroundColor Yellow }
Write-Host ''

# --- 6. start backend (single owned process tree) --------------------------------
# Child output is captured to backend\esw-backend.log so startup failures are
# diagnosable; the launcher prints the tail when the health gate fails.
$LogFile = Join-Path $BackendDir 'esw-backend.log'
$LogErrFile = Join-Path $BackendDir 'esw-backend.err.log'
try { Remove-Item $LogFile, $LogErrFile -ErrorAction SilentlyContinue } catch { }
$BackendArgs = @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$Port")
$Backend = Start-Process -FilePath $Py `
    -ArgumentList $BackendArgs `
    -WorkingDirectory $BackendDir -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $LogFile -RedirectStandardError $LogErrFile

Write-Host "  backend PID $($Backend.Id)   waiting for health..."
# poll /api/health (bounded, no sleep-bomb)
# NOTE: use curl.exe, not Invoke-RestMethod - Windows PowerShell 5.1's
# Invoke-RestMethod can hang for 100s in hidden/non-interactive processes
# (IE engine first-run init), which makes the health gate falsely fail
# even though the backend answers.  curl.exe is a Windows 11 builtin.
$ok = $false
for ($i = 0; $i -lt 90; $i++) {
    Start-Sleep -Milliseconds 500
    if ($Backend.HasExited) { break }
    try {
        $h = & curl.exe -s -m 2 "http://127.0.0.1:$Port/api/health" | ConvertFrom-Json
        if ($h.status -eq 'ok') { $ok = $true; break }
    } catch { }
}
if (-not $ok) {
    Write-Host '[FAIL] backend did not become healthy within 45 s.' -ForegroundColor Red
    foreach ($lf in @($LogFile, $LogErrFile)) {
        if (Test-Path $lf) {
            Write-Host "--- $lf (tail) ---" -ForegroundColor DarkGray
            Get-Content $lf -Tail 25 -ErrorAction SilentlyContinue
            Write-Host '--------------------------------------'
        }
    }
    if (-not $Backend.HasExited) { taskkill /PID $Backend.Id /T /F 2>$null | Out-Null }
    exit 1
}

# --- 7. live capability report ---------------------------------------------------------
$tier = 'TIER0'
$readiness = ''
$elev_req = $true
try {
    $tm = & curl.exe -s -m 3 "http://127.0.0.1:$Port/api/telemetry" | ConvertFrom-Json
    $tier = $tm.level
    $readiness = $tm.readiness
    $elev_req = $tm.elevation_required
} catch { }
Write-Host ''
Write-Host "ENCOMM SYSTEM WATCH $Version" -ForegroundColor Cyan
Write-Host ('-' * 34)
Write-Host "Backend:   http://127.0.0.1:$Port"
Write-Host 'Mode:      LIVE'
if ($readiness) { Write-Host "ETW:       $tier / $readiness" } else { Write-Host "ETW:       $tier" }
Write-Host "UI:        http://127.0.0.1:$Port"
Write-Host 'Read-only: YES'
if (-not $elevated) {
    Write-Host 'Network telemetry: TIER0 / limited - for full TIER2 ETW launch from an elevated PowerShell.' -ForegroundColor Yellow
}
Write-Host ''
Write-Host 'Press Ctrl+C to stop SYSTEM WATCH cleanly.' -ForegroundColor DarkGray

# --- 8. optional browser (convenience only) --------------------------------------------
if (-not $NoBrowser) {
    try { Start-Process "http://127.0.0.1:$Port" } catch { }
}

# --- 9. wait for exit, then clean up ONLY what we started ----------------------------------
try {
    Wait-Process -Id $Backend.Id -ErrorAction SilentlyContinue
} finally {
    if (Get-Process -Id $Backend.Id -ErrorAction SilentlyContinue) {
        taskkill /PID $Backend.Id /T /F 2>$null | Out-Null
    }
    Write-Host 'ENCOMM SYSTEM WATCH stopped.' -ForegroundColor Cyan
}