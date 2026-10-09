<#
.SYNOPSIS
  Creates or updates the Cloud Scheduler job that sweeps the run queue (the durable dispatch safety net).
.DESCRIPTION
  Every 30 minutes Cloud Scheduler POSTs /api/internal/sweep on the API with an OIDC token for qf-scheduler (audience
  = the service URL). The API checks the token itself (issuer, audience, e-mail); the sweep relaunches the worker
  when runs are waiting with no live lease, and fails runs whose worker vanished. Grants qf-scheduler run.invoker on
  the API service only. No retries: the next tick is the retry.
.EXAMPLE
  scripts\gcp\scheduler.ps1
  scripts\gcp\scheduler.ps1 -Pause      # stop sweeping (pause-compute.ps1 does this too)
  scripts\gcp\scheduler.ps1 -Resume
#>
param(
    [string]$Schedule = '*/30 * * * *',
    [switch]$Pause,
    [switch]$Resume,
    [string]$Project = 'quantfolio-511108',
    [string]$Account = 'sahildeshmukh1616@gmail.com',
    [string]$Region = 'asia-south1'
)
. (Join-Path $PSScriptRoot '_gcp.ps1')
Assert-Identity

if ($Pause) { Invoke-Gcloud scheduler jobs pause $SweepJob "--location=$Region" | Out-Null; Write-Host "$SweepJob paused."; exit 0 }
if ($Resume) { Invoke-Gcloud scheduler jobs resume $SweepJob "--location=$Region" | Out-Null; Write-Host "$SweepJob resumed."; exit 0 }

$number = (Invoke-Gcloud projects describe $Project '--format=value(projectNumber)' -Quiet).Trim()
$url = "https://$ApiService-$number.$Region.run.app"
Invoke-Gcloud run services add-iam-policy-binding $ApiService "--region=$Region" "--member=serviceAccount:$SchedulerSa" --role=roles/run.invoker '--format=none' | Out-Null

$common = @("--location=$Region", "--schedule=$Schedule", '--time-zone=Etc/UTC', "--uri=$url/api/internal/sweep",
    '--http-method=POST', "--oidc-service-account-email=$SchedulerSa", "--oidc-token-audience=$url",
    '--attempt-deadline=60s', '--max-retry-attempts=0')
if (Test-GcloudResource scheduler jobs describe $SweepJob "--location=$Region") {
    Invoke-Gcloud scheduler jobs update http $SweepJob @common | Out-Null
    Write-Host "$SweepJob updated ($Schedule UTC -> $url/api/internal/sweep)." -ForegroundColor Green
} else {
    Invoke-Gcloud scheduler jobs create http $SweepJob @common '--description=QuantFolio queue sweep (relaunch lost worker starts)' | Out-Null
    Write-Host "$SweepJob created ($Schedule UTC -> $url/api/internal/sweep)." -ForegroundColor Green
}
