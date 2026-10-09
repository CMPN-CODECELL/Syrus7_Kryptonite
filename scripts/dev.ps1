<#
.SYNOPSIS
  Development mode: API on 127.0.0.1:<ApiPort> in the background + Vite dev server
  (hot reload) on http://127.0.0.1:5173 in this window. Ctrl+C stops both.
.EXAMPLE
  scripts\dev.ps1            # real quantum core
  scripts\dev.ps1 -Fake      # deterministic fake pipeline (fast UI work, no simulation)
#>
param([int]$ApiPort = 8000, [switch]$Fake)
. (Join-Path $PSScriptRoot '_common.ps1')
Assert-Venv

if ($Fake) { $env:QF_PIPELINE_IMPL = 'fake' }
$env:QF_CORS_ORIGINS = 'http://127.0.0.1:5173,http://localhost:5173'
$env:QF_API_TARGET = "http://127.0.0.1:$ApiPort"

$startedHere = -not (Get-QfServer)
& (Join-Path $PSScriptRoot 'start.ps1') -Port $ApiPort -Background -NoBuild
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Push-Location -LiteralPath $Frontend
try {
    if (-not (Test-Path -LiteralPath (Join-Path $Frontend 'node_modules'))) { & npm ci }
    & npm run dev
} finally {
    Pop-Location
    if ($startedHere) { & (Join-Path $PSScriptRoot 'stop.ps1') }   # stop only the server this script started
}
