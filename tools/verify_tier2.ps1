<#
ENCOMM SYSTEM WATCH - TIER2 verification script (manual, non-elevating)

Verifies the real ETW TIER2 chain on this machine:
  /api/telemetry reports TIER2
  -> localhost traffic harness runs
  -> provider receives/drains events
  -> aggregator records and maps them to edges
  -> directional activity batches are emitted

IMPORTANT:
  - This script NEVER auto-elevates and never triggers UAC.
  - If the current session is not an Administrator PowerShell, it prints
    the required message and exits cleanly (TIER2 per-edge ETW telemetry
    requires elevation on Windows).
  - When run elevated, it uses whatever backend is already running on
    127.0.0.1:8765 (start the backend first, e.g. .\start-dev.ps1 or an
    elevated uvicorn).

Usage:
    powershell -ExecutionPolicy Bypass -File .\tools\verify_tier2.ps1
#>
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Api = 'http://127.0.0.1:8765'
$Port = 19736

function Test-Admin {
    $principal = New-Object Security.Principal.WindowsPrincipal(
        [Security.Principal.WindowsIdentity]::GetCurrent())
    return $principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-Json($Url) {
    return Invoke-RestMethod -Uri $Url -TimeoutSec 5
}

# ---------------------------------------------------------------- admin gate
if (-not (Test-Admin)) {
    Write-Host ''
    Write-Host 'TIER2 verification requires an Administrator PowerShell session.'
    Write-Host 'Per-edge ETW telemetry (Microsoft-Windows-TCPIP) is access-denied'
    Write-Host 'for unelevated processes on this machine. SYSTEM WATCH never'
    Write-Host 'auto-elevates and this script will not either.'
    Write-Host ''
    Write-Host 'Run instead:  Start-Process powershell -Verb RunAs -ArgumentList'
    Write-Host "               '-ExecutionPolicy Bypass -File ""$PSCommandPath""'"
    Write-Host ''
    exit 1
}

$fail = 0
function Check($Name, $Ok, $Detail) {
    if ($Ok) {
        Write-Host "  PASS  $Name - $Detail"
    } else {
        $script:fail++
        Write-Host "  FAIL  $Name - $Detail"
    }
}

Write-Host 'ENCOMM SYSTEM WATCH - TIER2 verification (elevated session)'
Write-Host ''

# ------------------------------------------------------------ backend alive
try {
    $health = Get-Json "$Api/api/health"
    Check 'backend alive' ($health.status -eq 'ok') "loop_ok=$($health.loop_ok)"
} catch {
    Write-Host "  FAIL  backend not reachable at $Api - start it first."
    exit 1
}

# ------------------------------------------------------------ capability
$tel = Get-Json "$Api/api/telemetry"
Check 'capability endpoint reports TIER2' ($tel.level -eq 'TIER2') "level=$($tel.level)"
if ($tel.level -ne 'TIER2') {
    Write-Host ''
    Write-Host "  Backend reports $($tel.level): $($tel.detail)"
    Write-Host '  TIER2 verification cannot proceed on this backend.'
    exit 1
}
Write-Host "  source: $($tel.source)"

# ------------------------------------------------------------ traffic
Write-Host ''
Write-Host "  launching localhost traffic harness on port $Port (10 s)..."
$Py = Join-Path $Root 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) { $Py = 'python' }
$harness = Start-Process -FilePath $Py -ArgumentList @(
    (Join-Path $Root 'tools\network_activity_test\run.py'),
    '--port', "$Port", '--watch', '10') -WorkingDirectory $Root `
    -WindowStyle Hidden -PassThru

# ------------------------------------------------------------ counters
Start-Sleep -Seconds 4
$dbg = Get-Json "$Api/api/telemetry/debug"
Check 'provider received events' ($dbg.provider.events_received -gt 0) "received=$($dbg.provider.events_received)"
Check 'provider drained events' ($dbg.provider.events_drained -gt 0) "drained=$($dbg.provider.events_drained)"
Check 'aggregator recorded events' ($dbg.aggregator.events_recorded -gt 0) "recorded=$($dbg.aggregator.events_recorded)"
Check 'events mapped to edges' ($dbg.aggregator.events_mapped_to_edges -gt 0) "mapped=$($dbg.aggregator.events_mapped_to_edges)"
Check 'activity batches emitted' ($dbg.aggregator.activity_batches_emitted -gt 0) "batches=$($dbg.aggregator.activity_batches_emitted)"
$lb = $dbg.aggregator.last_batch
Check 'directional bytes fwd > 0' ($lb.fwd_bytes -gt 0) "fwd=$($lb.fwd_bytes)"
Check 'directional bytes rev > 0' ($lb.rev_bytes -gt 0) "rev=$($lb.rev_bytes)"
Check 'queue bounded (depth)' ($dbg.provider.queue_depth -lt 20000) "depth=$($dbg.provider.queue_depth)"

if (-not $harness.HasExited) {
    Wait-Process -Id $harness.Id -Timeout 60 -ErrorAction SilentlyContinue
}

Write-Host ''
if ($fail -eq 0) {
    Write-Host 'RESULT: TIER2 chain verified (real ETW bytes -> provider -> aggregator -> edges -> batches).'
    exit 0
} else {
    Write-Host "RESULT: $fail check(s) FAILED - inspect the backend log and /api/telemetry/debug."
    exit 1
}
