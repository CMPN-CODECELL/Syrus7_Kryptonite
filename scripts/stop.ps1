<#
.SYNOPSIS
  Stops the QuantFolio server started by start.ps1 / dev.ps1 - and nothing else.
.DESCRIPTION
  Asks the server to shut down gracefully (data\state\api.stop). Only if it does not
  exit in time is its own process tree force-stopped. The PID comes from
  data\state\api.pid and is used only when the process start time matches the one
  recorded in that file, so no unrelated process can ever be stopped.
#>
param([int]$TimeoutSec = 25)
. (Join-Path $PSScriptRoot '_common.ps1')

$srv = Get-QfServer
if (-not $srv) {
    if (Test-Path -LiteralPath $PidFile) { Remove-Item -LiteralPath $PidFile -Force }   # stale file from a crashed run
    Write-Host 'QuantFolio is not running.'
    exit 0
}

Set-Content -LiteralPath $StopFile -Value 'stop' -Encoding ascii
if ($srv.Process.WaitForExit($TimeoutSec * 1000)) {
    Write-Host "QuantFolio stopped (PID $($srv.Pid))." -ForegroundColor Green
} else {
    $again = Get-QfServer   # re-verify identity immediately before forcing
    if ($again -and $again.Pid -eq $srv.Pid) {
        Write-Host 'The server did not stop in time; stopping its process tree.' -ForegroundColor Yellow
        & taskkill /PID $srv.Pid /T /F | Out-Null
    }
}
foreach ($f in @($PidFile, $StopFile)) {
    if (Test-Path -LiteralPath $f) { Remove-Item -LiteralPath $f -Force }
}
