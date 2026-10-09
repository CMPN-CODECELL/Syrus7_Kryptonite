<#
.SYNOPSIS
  Rolls the API service and/or the worker job back to an earlier image.
.DESCRIPTION
  API: sends 100% of traffic to an earlier revision (instant; no rebuild). Without -Revision it picks the newest
  ready revision before the one serving now. Worker: redeploys the job with an earlier image digest (Artifact Registry
  keeps the 10 most recent images; list them with -List).

  Schema compatibility: the API and worker share Firestore documents. Roll both back to the SAME image, and pause
  compute first (pause-compute.ps1) so no worker of the newer image is mid-run while the older one takes over:
    scripts\gcp\pause-compute.ps1 -Reason rollback; scripts\gcp\rollback.ps1 -Image <digest>; scripts\gcp\pause-compute.ps1 -Resume
.EXAMPLE
  scripts\gcp\rollback.ps1 -List
  scripts\gcp\rollback.ps1                                   # API to the previous revision, worker to that revision's image
  scripts\gcp\rollback.ps1 -Revision quantfolio-api-00003-abc
  scripts\gcp\rollback.ps1 -Image asia-south1-docker.pkg.dev/quantfolio-511108/quantfolio/app@sha256:...
#>
param(
    [string]$Revision = '',
    [string]$Image = '',
    [switch]$ApiOnly,
    [switch]$WorkerOnly,
    [switch]$List,
    [string]$Project = 'quantfolio-511108',
    [string]$Account = 'sahildeshmukh1616@gmail.com',
    [string]$Region = 'asia-south1'
)
. (Join-Path $PSScriptRoot '_gcp.ps1')
Assert-Identity

$revs = Invoke-Gcloud run revisions list "--service=$ApiService" "--region=$Region" '--sort-by=~metadata.creationTimestamp' '--format=json' -Quiet | ConvertFrom-Json
if ($List) {
    Write-Host 'API revisions (newest first):'
    $revs | ForEach-Object { Write-Host ("  {0}  {1}  {2}" -f $_.metadata.name, $_.metadata.creationTimestamp, $_.spec.containers[0].image) }
    Write-Host 'Images in Artifact Registry (newest first):'
    Invoke-Gcloud artifacts docker images list $ImageBase '--include-tags' '--sort-by=~UPDATE_TIME' '--limit=10' '--format=table(version,tags,updateTime)' -Quiet | Write-Host
    exit 0
}

if ($Image -and $Image -notmatch '@sha256:[0-9a-f]{64}$') { Stop-With '-Image must be <repo>@sha256:<digest>.' }

if (-not $WorkerOnly) {
    if (-not $Revision) {
        if ($Image) {
            $match = $revs | Where-Object { $_.spec.containers[0].image -eq $Image } | Select-Object -First 1
            if ($match) { $Revision = $match.metadata.name }
        } else {
            $svc = Invoke-Gcloud run services describe $ApiService "--region=$Region" '--format=json' -Quiet | ConvertFrom-Json
            $serving = @($svc.status.traffic | Where-Object { $_.percent -eq 100 } | ForEach-Object { $_.revisionName })[0]
            $older = $revs | Where-Object { $_.metadata.name -ne $serving } |
                Where-Object { ($_.status.conditions | Where-Object { $_.type -eq 'Ready' }).status -eq 'True' } |
                Where-Object { [datetime]$_.metadata.creationTimestamp -lt [datetime](($revs | Where-Object { $_.metadata.name -eq $serving }).metadata.creationTimestamp) } |
                Select-Object -First 1
            if (-not $older) { Stop-With "No ready revision older than $serving." }
            $Revision = $older.metadata.name
        }
    }
    if ($Revision) {
        Invoke-Gcloud run services update-traffic $ApiService "--region=$Region" "--to-revisions=$Revision=100" | Out-Null
        if (-not $Image) { $Image = ($revs | Where-Object { $_.metadata.name -eq $Revision }).spec.containers[0].image }
        Save-Record @{ api_rollback = @{ revision = $Revision; image = $Image } }
        Write-Host "API traffic: 100% to $Revision." -ForegroundColor Green
    } elseif ($Image) {
        Write-Host 'No existing API revision runs that image; deploying it as a new revision.'
        & (Join-Path $PSScriptRoot 'deploy-api.ps1') -Image $Image -Project $Project -Account $Account -Region $Region
    }
}

if (-not $ApiOnly) {
    if (-not $Image) { Stop-With 'Worker rollback needs -Image (or an API revision to take the image from).' }
    & (Join-Path $PSScriptRoot 'deploy-worker.ps1') -Image $Image -Project $Project -Account $Account -Region $Region
}
