<#
.SYNOPSIS
  Deploys the worker Cloud Run Job (quantfolio-worker) from an image digest.
.DESCRIPTION
  One task, parallelism 1, no automatic retries (a failed execution is reported, never silently re-run), 2 vCPU,
  4 GiB, explicit task timeout. The API starts executions through the Cloud Run Admin API with no overrides, so a
  run always gets exactly this configuration. Grants qf-api run.invoker on this job only.
.EXAMPLE
  scripts\gcp\deploy-worker.ps1                      # the image recorded by build.ps1
  scripts\gcp\deploy-worker.ps1 -Image asia-south1-docker.pkg.dev/quantfolio-511108/quantfolio/app@sha256:...
#>
param(
    [string]$Image = '',
    [string]$Cpu = '2',
    [string]$Memory = '4Gi',
    [int]$TaskTimeoutS = 7200,
    [int]$WorkerBudgetS = 6600,      # the worker stops claiming new runs when one would not fit in what is left
    [switch]$ComputeDisabled,
    [string]$Project = 'quantfolio-511108',
    [string]$Account = 'sahildeshmukh1616@gmail.com',
    [string]$Region = 'asia-south1'
)
. (Join-Path $PSScriptRoot '_gcp.ps1')
Assert-Identity
if (-not $Image) { $Image = Get-Record 'image' }
if (-not $Image -or $Image -notmatch '@sha256:[0-9a-f]{64}$') { Stop-With 'Pass -Image <repo>@sha256:<digest> or run build.ps1 first.' }

$saltVersion = (Invoke-Gcloud secrets versions list $SaltSecret '--filter=state=ENABLED' '--sort-by=~createTime' '--limit=1' '--format=value(name.basename())' -Quiet).Trim()
if (-not $saltVersion) { Stop-With "Secret $SaltSecret has no enabled version. Run provision.ps1." }
$computeOn = if ($ComputeDisabled) { 'false' } else { 'true' }
$envVars = @(
    "QF_GCP_PROJECT=$Project", "QF_GCP_REGION=$Region", "QF_BUCKET=$Bucket", "QF_WORKER_JOB=$WorkerJob",
    "QF_COMPUTE_ENABLED=$computeOn", "QF_WORKER_BUDGET_S=$WorkerBudgetS", 'QF_EXPERIMENT_TIMEOUT_S=1800',
    'QF_DAILY_RUN_CAP=20', 'QF_USER_DAILY_RUN_CAP=5', 'QF_USER_ACTIVE_CAP=1', 'QF_QUEUE_CAPACITY=6', 'QF_RETENTION_DAYS=30',
    'QF_START_WORKER=false', 'QF_WARM_WORKER=false', 'QF_LOG_LEVEL=INFO'
) -join '~'

Invoke-Gcloud run jobs deploy $WorkerJob "--image=$Image" "--region=$Region" "--service-account=$WorkerSa" `
    "--cpu=$Cpu" "--memory=$Memory" '--tasks=1' '--parallelism=1' '--max-retries=0' "--task-timeout=$($TaskTimeoutS)s" `
    '--command=python' '--args=-m,quantfolio.cloud.worker' "--set-env-vars=^~^$envVars" `
    "--set-secrets=QF_IP_SALT=$($SaltSecret):$saltVersion" '--labels=app=quantfolio,role=worker' | Out-Null
Invoke-Gcloud run jobs add-iam-policy-binding $WorkerJob "--region=$Region" "--member=serviceAccount:$ApiSa" --role=roles/run.invoker '--format=none' | Out-Null

$cfg = Invoke-Gcloud run jobs describe $WorkerJob "--region=$Region" '--format=json' -Quiet | ConvertFrom-Json
$task = $cfg.spec.template.spec.template.spec
Save-Record @{ worker = @{ image = $Image; cpu = $Cpu; memory = $Memory; tasks = 1; parallelism = 1; max_retries = 0
        task_timeout_s = $TaskTimeoutS; salt_version = $saltVersion; generation = $cfg.metadata.generation } }
Write-Host "Worker job $WorkerJob deployed: $Cpu vCPU / $Memory, 1 task, no retries, timeout $($TaskTimeoutS)s, generation $($cfg.metadata.generation)." -ForegroundColor Green
if ($task.containers[0].image -ne $Image) { Write-Host "Warning: the job reports image $($task.containers[0].image)" -ForegroundColor Yellow }
