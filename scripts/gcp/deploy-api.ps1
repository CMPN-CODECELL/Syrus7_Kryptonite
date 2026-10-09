<#
.SYNOPSIS
  Deploys the API Cloud Run service (quantfolio-api) from an image digest.
.DESCRIPTION
  Public (allUsers may invoke: Firebase Hosting's /api rewrite needs it, and the app has no sign-in); abuse is
  bounded inside the app (20 live runs per day in total, 5 per client network, one active run per client, a bounded
  queue, request-size and qubit limits, a kill switch). 1 vCPU / 1 GiB, min 0 / max 2 instances, 60 s request
  timeout (event streams end after 50 s and the browser reconnects). Debug/docs endpoints are not served.
  The new revision gets all traffic only after it starts healthy; the previous one stays for rollback.ps1.
.EXAMPLE
  scripts\gcp\deploy-api.ps1
  scripts\gcp\deploy-api.ps1 -ClientIpSource right:1     # after checking which X-Forwarded-For entry is the visitor
#>
param(
    [string]$Image = '',
    [string]$ClientIpSource = 'right:2',
    [int]$MaxInstances = 2,
    [int]$Concurrency = 40,
    [switch]$ComputeDisabled,
    [switch]$NoTraffic,              # deploy a tagged revision without sending traffic to it (smoke test first)
    [string]$Project = 'quantfolio-511108',
    [string]$Account = 'sahildeshmukh1616@gmail.com',
    [string]$Region = 'asia-south1'
)
. (Join-Path $PSScriptRoot '_gcp.ps1')
Assert-Identity
if (-not $Image) { $Image = Get-Record 'image' }
if (-not $Image -or $Image -notmatch '@sha256:[0-9a-f]{64}$') { Stop-With 'Pass -Image <repo>@sha256:<digest> or run build.ps1 first.' }

$number = (Invoke-Gcloud projects describe $Project '--format=value(projectNumber)' -Quiet).Trim()
$url = "https://$ApiService-$number.$Region.run.app"    # deterministic service URL: the sweep's OIDC audience
$saltVersion = (Invoke-Gcloud secrets versions list $SaltSecret '--filter=state=ENABLED' '--sort-by=~createTime' '--limit=1' '--format=value(name.basename())' -Quiet).Trim()
if (-not $saltVersion) { Stop-With "Secret $SaltSecret has no enabled version. Run provision.ps1." }
$computeOn = if ($ComputeDisabled) { 'false' } else { 'true' }
$hosts = "*.run.app,$Project.web.app,$Project.firebaseapp.com,*.web.app"   # *.web.app covers Hosting preview channels
$envVars = @(
    "QF_GCP_PROJECT=$Project", "QF_GCP_REGION=$Region", "QF_BUCKET=$Bucket", "QF_WORKER_JOB=$WorkerJob",
    "QF_COMPUTE_ENABLED=$computeOn", 'QF_DAILY_RUN_CAP=20', 'QF_USER_DAILY_RUN_CAP=5', 'QF_USER_ACTIVE_CAP=1',
    'QF_QUEUE_CAPACITY=6', 'QF_RETENTION_DAYS=30', "QF_CLIENT_IP_SOURCE=$ClientIpSource", "QF_ALLOWED_HOSTS=$hosts",
    "QF_SWEEP_SA=$SchedulerSa", "QF_SWEEP_AUDIENCE=$url", 'QF_LIVE_STREAM_MAX_S=50', 'QF_START_WORKER=false',
    'QF_WARM_WORKER=false', 'QF_LOG_LEVEL=INFO'
) -join '~'

$traffic = @()
if ($NoTraffic) { $traffic = @('--no-traffic', '--tag=candidate') }
Invoke-Gcloud run deploy $ApiService "--image=$Image" "--region=$Region" "--service-account=$ApiSa" '--cpu=1' '--memory=1Gi' `
    '--min-instances=0' "--max-instances=$MaxInstances" "--concurrency=$Concurrency" '--timeout=60' '--cpu-boost' `
    '--port=8080' '--allow-unauthenticated' '--ingress=all' "--set-env-vars=^~^$envVars" `
    "--set-secrets=QF_IP_SALT=$($SaltSecret):$saltVersion" '--labels=app=quantfolio,role=api' @traffic | Out-Null

# allUsers must hold run.invoker for the public site; an organisation policy (domain restricted sharing) can refuse it.
$policy = Invoke-Gcloud run services get-iam-policy $ApiService "--region=$Region" '--format=json' -Quiet | ConvertFrom-Json
$public = @($policy.bindings | Where-Object { $_.role -eq 'roles/run.invoker' -and $_.members -contains 'allUsers' }).Count -gt 0
if (-not $public) {
    Stop-With 'The service is deployed but allUsers could not be given run.invoker (an organisation policy may forbid it). The site cannot reach the API. Organisation policies were NOT changed.'
}

$svc = Invoke-Gcloud run services describe $ApiService "--region=$Region" '--format=json' -Quiet | ConvertFrom-Json
$revision = $svc.status.latestCreatedRevisionName
Save-Record @{ api = @{ image = $Image; revision = $revision; url = $url; client_ip_source = $ClientIpSource
        salt_version = $saltVersion; max_instances = $MaxInstances; concurrency = $Concurrency } }
Write-Host "API $ApiService revision $revision at $url (public invoke; min 0 / max $MaxInstances)." -ForegroundColor Green
