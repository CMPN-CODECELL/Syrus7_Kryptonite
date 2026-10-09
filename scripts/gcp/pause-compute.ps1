<#
.SYNOPSIS
  Pauses (or resumes) all paid compute of the hosted demo: the kill switch.
.DESCRIPTION
  Default (instant, no redeploy): writes the Firestore document control/compute {enabled:false}. The API refuses
  new runs at once (503 compute_paused) and a worker that starts runs nothing; the scheduled sweep is paused too.
  Finished runs stay readable and the web site keeps working.

  -Redeploy also rolls out QF_COMPUTE_ENABLED=false to the API service and the worker job (a new revision; survives a
  Firestore mistake). Running work: a run already executing finishes or is cancelled from the site; to stop it
  hard, cancel the job execution (shown at the end).

  Resume with -Resume (undoes both; the next status read or sweep relaunches the worker for waiting runs).
.EXAMPLE
  scripts\gcp\pause-compute.ps1 -Reason "budget alert"
  scripts\gcp\pause-compute.ps1 -Redeploy -Reason "incident"
  scripts\gcp\pause-compute.ps1 -Resume
#>
param(
    [switch]$Resume,
    [switch]$Redeploy,
    [string]$Reason = 'paused by the operator',
    [string]$Project = 'quantfolio-511108',
    [string]$Account = 'sahildeshmukh1616@gmail.com',
    [string]$Region = 'asia-south1'
)
. (Join-Path $PSScriptRoot '_gcp.ps1')
Assert-Identity
$enabled = [bool]$Resume

# 1. Firestore control/compute through the REST API (token kept in memory, never printed).
$token = (& gcloud auth print-access-token --account $Account 2>$null)
if (-not $token) { Stop-With "Could not get an access token for $Account." }
$doc = "https://firestore.googleapis.com/v1/projects/$Project/databases/(default)/documents/control/compute"
$now = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fff+00:00')
$reasonValue = if ($enabled) { @{ nullValue = $null } } else { @{ stringValue = $Reason } }
$body = @{ fields = @{ enabled = @{ booleanValue = $enabled }; reason = $reasonValue
        updated_at = @{ stringValue = $now }; updated_by = @{ stringValue = 'scripts/gcp/pause-compute.ps1' } } } | ConvertTo-Json -Depth 5
$headers = @{ Authorization = "Bearer $token"; 'X-Goog-User-Project' = $Project }
try {
    Invoke-RestMethod -Method Patch -Uri $doc -Headers $headers -ContentType 'application/json' -Body $body | Out-Null
} finally { $token = $null; $headers = $null }
Write-Host "control/compute enabled=$enabled"

# 2. Optional env switch (new revisions; takes ~1 minute).
if ($Redeploy -or $Resume) {
    $value = if ($enabled) { 'true' } else { 'false' }
    $svc = Invoke-Gcloud run services describe $ApiService "--region=$Region" '--format=value(spec.template.spec.containers[0].env)' -AllowFail -Quiet
    if ($Redeploy -or $svc -match 'QF_COMPUTE_ENABLED.{0,20}false') {
        Invoke-Gcloud run services update $ApiService "--region=$Region" "--update-env-vars=QF_COMPUTE_ENABLED=$value" | Out-Null
        Invoke-Gcloud run jobs update $WorkerJob "--region=$Region" "--update-env-vars=QF_COMPUTE_ENABLED=$value" | Out-Null
        Write-Host "QF_COMPUTE_ENABLED=$value on $ApiService and $WorkerJob"
    }
}

# 3. Scheduled sweep.
if ($enabled) { Invoke-Gcloud scheduler jobs resume $SweepJob "--location=$Region" -AllowFail | Out-Null } else { Invoke-Gcloud scheduler jobs pause $SweepJob "--location=$Region" -AllowFail | Out-Null }

if (-not $enabled) {
    Write-Host 'Compute paused. To stop a worker execution that is running right now:' -ForegroundColor Yellow
    Write-Host "  gcloud run jobs executions list --job=$WorkerJob --region=$Region --project $Project --account $Account"
    Write-Host "  gcloud run jobs executions cancel <execution> --region=$Region --project $Project --account $Account"
} else {
    Write-Host 'Compute resumed.' -ForegroundColor Green
}
