<#
.SYNOPSIS
  Starts QuantFolio (API + job worker + built web app) on http://127.0.0.1:<Port>.
.DESCRIPTION
  Checks the Python environment and the port, builds the web app if it has not been
  built, then runs the server. The server writes data\state\api.pid; stop it with
  Ctrl+C (foreground) or scripts\stop.ps1.
.EXAMPLE
  scripts\start.ps1                 # foreground, port 8000
  scripts\start.ps1 -Background -Open
#>
param(
    [int]$Port = 8000,
    [switch]$Background,   # return once the server is ready instead of blocking
    [switch]$Open,         # open the browser when ready (with -Background)
    [switch]$Rebuild,      # force a frontend build
    [switch]$NoBuild       # never build (API still works; the web app needs frontend\dist)
)
. (Join-Path $PSScriptRoot '_common.ps1')
Assert-Venv

$running = Get-QfServer
if ($running) {
    Write-Host "QuantFolio is already running at http://127.0.0.1:$($running.Port) (PID $($running.Pid)). Use scripts\stop.ps1 to stop it."
    exit 0
}
if (-not (Test-PortFree $Port)) {
    Write-Host "Port $Port is already in use by another program. Pick another one:  scripts\start.ps1 -Port 8010" -ForegroundColor Red
    exit 4
}

$dist = Join-Path $Frontend 'dist\index.html'
if ($Rebuild -or (-not $NoBuild -and -not (Test-Path -LiteralPath $dist))) {
    Write-Host 'Building the web app...'
    Invoke-FrontendBuild
}

if (-not $Background) {
    Push-Location -LiteralPath $Backend
    try { & $Py -m quantfolio.serve --port $Port } finally { Pop-Location }
    exit $LASTEXITCODE
}

$logDir = Join-Path $DataDir 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$p = Start-Process -FilePath $Py -ArgumentList @('-m', 'quantfolio.serve', '--port', "$Port") -WorkingDirectory $Backend `
    -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir 'serve.out.log') `
    -RedirectStandardError (Join-Path $logDir 'serve.err.log')
if (-not (Wait-QfReady $Port 120)) {
    Write-Host "The server did not become ready. See $logDir\serve.err.log" -ForegroundColor Red
    if (-not $p.HasExited) { & taskkill /PID $p.Id /T /F | Out-Null }   # only the process this script started
    exit 1
}
Write-Host "QuantFolio is running at http://127.0.0.1:$Port  (API docs: /api/docs). Stop it with scripts\stop.ps1" -ForegroundColor Green
if ($Open) { Start-Process "http://127.0.0.1:$Port/" }

exit 0
