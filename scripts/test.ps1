<#
.SYNOPSIS
  Runs the test suites and writes evidence (JUnit XML + text) under evidence\.
.DESCRIPTION
  Backend tests run in ONE process (the laptop has little free RAM). Nothing here
  contacts Yahoo, IBM or any other third party.
.EXAMPLE
  scripts\test.ps1               # core + API + frontend unit tests
  scripts\test.ps1 -Suite api -Fast
#>
param(
    [ValidateSet('all', 'core', 'api', 'frontend')][string]$Suite = 'all',
    [switch]$Fast   # skip tests marked slow / real
)
. (Join-Path $PSScriptRoot '_common.ps1')
Assert-Venv
$failed = 0

function Invoke-Pytest([string]$Name) {
    $out = Join-Path $Root "evidence\$Name-tests"
    New-Item -ItemType Directory -Force -Path $out | Out-Null
    $pyArgs = @('-m', 'pytest', "tests/$Name", '-q', '-p', 'no:cacheprovider', "--junitxml=$(Join-Path $out 'junit.xml')")
    if ($Fast) { $pyArgs += @('-m', 'not slow and not real') }
    Push-Location -LiteralPath $Backend
    try {
        # "$_" turns stderr records into plain text; the file is saved as UTF-8 (Tee-Object -FilePath would write UTF-16).
        & $Py @pyArgs 2>&1 | ForEach-Object { "$_" } | Tee-Object -Variable lines
        $code = $LASTEXITCODE
        $lines | Set-Content -LiteralPath (Join-Path $out 'pytest.txt') -Encoding utf8
        if ($code -ne 0) { $script:failed++ }
    } finally { Pop-Location }
}

$ErrorActionPreference = 'Continue'   # test output on stderr must not abort the script
if ($Suite -in @('all', 'core')) { Invoke-Pytest 'core' }
if ($Suite -in @('all', 'api')) { Invoke-Pytest 'api' }
if ($Suite -in @('all', 'frontend')) {
    Push-Location -LiteralPath $Frontend
    try {
        & npm run typecheck
        if ($LASTEXITCODE -ne 0) { $failed++ }
        & npm test
        if ($LASTEXITCODE -ne 0) { $failed++ }
    } finally { Pop-Location }
}
if ($failed -gt 0) { Write-Host "$failed suite(s) failed." -ForegroundColor Red; exit 1 }
Write-Host 'All selected suites passed.' -ForegroundColor Green
