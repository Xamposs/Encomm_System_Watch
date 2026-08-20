<#
ENCOMM SYSTEM WATCH - one-time setup / bootstrap (Windows 11)

Prepares a fresh clone for release use WITHOUT touching system software:
  - creates the backend virtualenv (backend\.venv) if absent
  - installs backend dependencies (requirements.txt)
  - installs pinned frontend dependencies (npm ci)
  - builds the production frontend (npm run build -> frontend\dist)

It NEVER: installs/modifies system software, edits the firewall, changes
Windows security settings, touches Docker/WSL/hypervisors/GPU drivers,
enables Windows features, or auto-elevates.

External prerequisites (NOT installed by this script):
  - Windows 11
  - Python 3.11+      (on PATH, or python3/py)
  - Node.js 18+       (npm on PATH) - only required to build the frontend
  - NVIDIA driver     (optional - only for GPU telemetry)

Usage:
    .\Setup-SystemWatch.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Join-Path $Root 'backend'
$FrontendDir = Join-Path $Root 'frontend'
$VenvPy = Join-Path $BackendDir '.venv\Scripts\python.exe'

Write-Host ''
Write-Host 'ENCOMM SYSTEM WATCH - setup' -ForegroundColor Cyan
Write-Host ('-' * 34)

# --- Python -----------------------------------------------------------------
function Find-Python {
    foreach ($cand in @('python', 'python3', 'py')) {
        try {
            $v = & $cand -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0 -and $v -match '^3\.(1[1-9]|[2-9][0-9])') {
                return @{ Path = (Get-Command $cand).Source; Version = $v }
            }
        } catch { }
    }
    return $null
}

if (Test-Path $VenvPy) {
    $v = & $VenvPy -c "import sys; print('%d.%d' % sys.version_info[:2])"
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[FAIL] backend\.venv exists but its python is broken - remove it and rerun.' -ForegroundColor Red
        exit 1
    }
    Write-Host "backend venv found: Python $v"
} else {
    $found = Find-Python
    if (-not $found) {
        Write-Host '[FAIL] Python 3.11+ not found on PATH (tried python, python3, py).' -ForegroundColor Red
        Write-Host '       Install Python 3.11+ from https://www.python.org/downloads/ and rerun.'
        exit 1
    }
    Write-Host "creating backend virtualenv with $($found.Path) (Python $($found.Version)) ..."
    & $found.Path -m venv (Join-Path $BackendDir '.venv')
    if ($LASTEXITCODE -ne 0) { Write-Host '[FAIL] venv creation failed.' -ForegroundColor Red; exit 1 }
}

# --- backend dependencies ---
Write-Host 'installing backend dependencies ...'
$env:PYTHONPATH = ''   # never inherit a leaking PYTHONPATH into the venv
& $VenvPy -m pip install --disable-pip-version-warning -q -r (Join-Path $BackendDir 'requirements.txt')
if ($LASTEXITCODE -ne 0) { Write-Host '[FAIL] backend dependency install failed.' -ForegroundColor Red; exit 1 }

# --- Node.js ---
$npm = (Get-Command npm -ErrorAction SilentlyContinue)
if (-not $npm) {
    Write-Host '[FAIL] npm not found on PATH. Install Node.js 18+ from https://nodejs.org/ and rerun.' -ForegroundColor Red
    exit 1
}
$nodeVer = (& node --version 2>$null)
Write-Host "Node $nodeVer found"

# --- frontend: pinned install + production build ---
Write-Host 'installing frontend dependencies (npm ci) ...'
Push-Location $FrontendDir
try {
    & npm.cmd ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }
    Write-Host 'building production frontend (npm run build) ...'
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'npm run build failed' }
} finally {
    Pop-Location
}
if (-not (Test-Path (Join-Path $FrontendDir 'dist\index.html'))) {
    Write-Host '[FAIL] frontend build did not produce dist\index.html.' -ForegroundColor Red
    exit 1
}

Write-Host ''
Write-Host 'SETUP COMPLETE.' -ForegroundColor Green
Write-Host 'Run:  .\Start-SystemWatch.ps1'
Write-Host 'For full TIER2 ETW (per-edge bytes), open an ADMINISTRATOR PowerShell first.'
Write-Host 'Non-admin is fine too: the app starts and reports TIER0 truthfully.'
Write-Host ''