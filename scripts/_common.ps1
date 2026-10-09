# Shared helpers for the QuantFolio scripts (dot-sourced). Works on Windows PowerShell 5.1.
# Every path is quoted/joined so the project may live in a folder with spaces.
$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'
$Py = Join-Path $Backend '.venv\Scripts\python.exe'
$DataDir = Join-Path $Root 'data'
if ($env:QF_DATA_DIR) { $DataDir = $env:QF_DATA_DIR }
$StateDir = Join-Path $DataDir 'state'
$PidFile = Join-Path $StateDir 'api.pid'
$StopFile = Join-Path $StateDir 'api.stop'

# Keep package caches and temp files inside the project folder (same drive as the project).
$env:PIP_CACHE_DIR = Join-Path $Root '.cache\pip'
$env:npm_config_cache = Join-Path $Root '.cache\npm'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $Root '.cache\playwright'
$tmp = Join-Path $Root '.tmp'
if (-not (Test-Path -LiteralPath $tmp)) { New-Item -ItemType Directory -Force -Path $tmp | Out-Null }
$env:TEMP = $tmp
$env:TMP = $tmp

function Assert-Venv {
    if (-not (Test-Path -LiteralPath $Py)) {
        Write-Host 'The Python environment is missing. Run scripts\setup.ps1 first.' -ForegroundColor Red
        exit 2
    }
}

function Test-PortFree([int]$Port) {
    $listener = $null
    try {
        $listener = New-Object System.Net.Sockets.TcpListener ([System.Net.IPAddress]::Loopback, $Port)
        $listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        if ($null -ne $listener) { $listener.Stop() }
    }
}

function Get-QfServer {
    # The server recorded in api.pid, or $null. The PID is trusted only if that process is a
    # Python process with the same start time as recorded, so an unrelated process that
    # happens to reuse the PID is never touched.
    if (-not (Test-Path -LiteralPath $PidFile)) { return $null }
    try { $info = Get-Content -LiteralPath $PidFile -Raw | ConvertFrom-Json } catch { return $null }
    $p = Get-Process -Id ([int]$info.pid) -ErrorAction SilentlyContinue
    if ($null -eq $p) { return $null }
    if ($p.ProcessName -notlike 'python*') { return $null }
    $epoch = New-Object DateTime (1970, 1, 1, 0, 0, 0, [DateTimeKind]::Utc)
    $started = ($p.StartTime.ToUniversalTime() - $epoch).TotalSeconds
    if ([math]::Abs($started - [double]$info.create_time) -gt 2) { return $null }
    return [pscustomobject]@{ Pid = [int]$info.pid; Port = [int]$info.port; Process = $p }
}

function Wait-QfReady([int]$Port, [int]$TimeoutSec = 90) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/ready" -TimeoutSec 3
            if ($r.checks.worker) { return $true }
        } catch { }
        Start-Sleep -Milliseconds 400
    }
    return $false
}

function Invoke-FrontendBuild {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Write-Host 'Node.js/npm was not found; install Node 20+ to build the web app.' -ForegroundColor Red
        exit 2
    }
    Push-Location -LiteralPath $Frontend
    try {
        if (-not (Test-Path -LiteralPath (Join-Path $Frontend 'node_modules'))) {
            & npm ci
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        }
        & npm run build
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } finally {
        Pop-Location
    }
}
