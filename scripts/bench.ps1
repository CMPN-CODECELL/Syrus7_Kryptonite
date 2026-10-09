<#
.SYNOPSIS
  Load / stress benchmark of the platform. Results go to evidence\load\.
.DESCRIPTION
  Starts its own isolated server (own port and data folder under .tmp\) and stops
  only that server. Uses the deterministic fake pipeline for the load stages and a
  few genuine Quick jobs on the local simulator. Never touches Yahoo, IBM or any
  third party, and never touches a QuantFolio server you started yourself.
.EXAMPLE
  scripts\bench.ps1              # full bounded run (~10 minutes)
  scripts\bench.ps1 -Smoke       # ~1 minute sanity pass
  scripts\bench.ps1 -Real        # only the genuine Quick jobs
#>
param([switch]$Smoke, [switch]$Real)
. (Join-Path $PSScriptRoot '_common.ps1')
Assert-Venv
$ErrorActionPreference = 'Continue'
Push-Location -LiteralPath $Backend
try {
    if ($Real) {
        & $Py (Join-Path $Backend 'loadtest\real_quick.py') --jobs 3
    } elseif ($Smoke) {
        & $Py (Join-Path $Backend 'loadtest\run_load.py') --smoke
    } else {
        & $Py (Join-Path $Backend 'loadtest\run_load.py')
        if ($LASTEXITCODE -eq 0) { & $Py (Join-Path $Backend 'loadtest\sse_latency.py') }
        if ($LASTEXITCODE -eq 0) { & $Py (Join-Path $Backend 'loadtest\real_quick.py') --jobs 3 }
    }
    exit $LASTEXITCODE
} finally { Pop-Location }
