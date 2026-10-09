<#
.SYNOPSIS
  One-time setup: checks prerequisites, creates backend\.venv, installs the pinned
  Python packages, installs frontend packages and builds the web app.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File "scripts\setup.ps1"
#>
param([switch]$SkipFrontend)
. (Join-Path $PSScriptRoot '_common.ps1')

# --- prerequisites -----------------------------------------------------------
$sysPy = Get-Command python -ErrorAction SilentlyContinue
if (-not $sysPy -and -not (Test-Path -LiteralPath $Py)) {
    Write-Host 'Python 3.11+ was not found on PATH. Install it from python.org and re-run.' -ForegroundColor Red
    exit 2
}
if (-not (Test-Path -LiteralPath $Py)) {
    $ver = & python -c "import sys; print('%d.%d' % sys.version_info[:2])"
    if ([version]$ver -lt [version]'3.11') {
        Write-Host "Python $ver found; 3.11 or newer is required." -ForegroundColor Red
        exit 2
    }
    Write-Host "Creating backend\.venv (Python $ver)..."
    & python -m venv (Join-Path $Backend '.venv')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

# --- backend packages (pinned lock file; cache stays in .cache\pip) ----------
Write-Host 'Installing Python packages from requirements.lock.txt...'
& $Py -m pip install --disable-pip-version-check -r (Join-Path $Backend 'requirements.lock.txt')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# --- local settings file (never contains a real key) -------------------------
$envFile = Join-Path $Root '.env'
if (-not (Test-Path -LiteralPath $envFile)) {
    Copy-Item -LiteralPath (Join-Path $Root '.env.example') -Destination $envFile
    Write-Host 'Created .env from .env.example (IBM access is optional; leave the token empty to stay local).'
}

# --- frontend ----------------------------------------------------------------
if (-not $SkipFrontend) {
    Write-Host 'Installing frontend packages and building the web app...'
    Invoke-FrontendBuild
}

Write-Host ''
Write-Host 'Setup complete. Start the app with:  scripts\start.ps1' -ForegroundColor Green
