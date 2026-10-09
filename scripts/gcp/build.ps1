<#
.SYNOPSIS
  Builds the API/worker image with Cloud Build (no local Docker) and records its digest.
.DESCRIPTION
  Uploads only what .gcloudignore allows (backend code, the hashed lock file, the Dockerfile, bundled market data),
  builds backend\Dockerfile as the qf-build service account and pushes to Artifact Registry. The tag names the
  source checkpoint: the git commit plus "-dirty" when the working tree has uncommitted changes (read-only git).
  The digest is written to deploy\last-deploy.json; deploy-api.ps1 and deploy-worker.ps1 deploy by digest.
.EXAMPLE
  scripts\gcp\build.ps1
#>
param(
    [string]$Tag = '',
    [string]$Project = 'quantfolio-511108',
    [string]$Account = 'sahildeshmukh1616@gmail.com',
    [string]$Region = 'asia-south1'
)
. (Join-Path $PSScriptRoot '_gcp.ps1')
Assert-Identity

$old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
$commit = (& git -C $Root rev-parse --short=7 HEAD 2>$null)
$dirty = (& git -C $Root status --porcelain 2>$null | Measure-Object).Count -gt 0
$ErrorActionPreference = $old
if (-not $commit) { $commit = 'nogit' }
$source = if ($dirty) { "$commit-dirty" } else { $commit }
if (-not $Tag) { $Tag = "$source-$((Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss'))" }

Write-Host "Building $ImageBase`:$Tag from $Root (source $source)..."
$build = Invoke-Gcloud builds submit $Root "--config=$(Join-Path $Backend 'cloudbuild.yaml')" "--region=$Region" `
    "--substitutions=_TAG=$Tag,_SOURCE=$source" "--service-account=projects/$Project/serviceAccounts/$BuildSa" `
    "--gcs-source-staging-dir=gs://$Project-build/source" '--format=value(id)'
$buildId = ($build -split "`n" | Where-Object { $_ -match '^[0-9a-f-]{36}$' } | Select-Object -Last 1)
$digest = Invoke-Gcloud artifacts docker images describe "$ImageBase`:$Tag" '--format=value(image_summary.digest)' -Quiet
if (-not $digest) { Stop-With 'The image was not found in Artifact Registry after the build.' }
Save-Record @{ image = "$ImageBase@$digest"; tag = $Tag; source = $source; build_id = $buildId }
Write-Host "Image: $ImageBase@$digest (tag $Tag, build $buildId)" -ForegroundColor Green
