<#
.SYNOPSIS
  Creates (or checks) the Google Cloud resources of the hosted QuantFolio demo. Safe to run again.
.DESCRIPTION
  Every step first checks what exists, so a second run changes nothing. It never creates a project, never links or
  changes billing, never changes organisation policies and never deploys code. Steps:

    1. required APIs                        6. Firestore TTL on expire_at (30-day retention)
    2. service accounts (least privilege)    7. private bucket for uploads/results + build staging bucket
    3. Artifact Registry repo + cleanup      8. Secret Manager secret qf-ip-salt (random, never printed)
    4. Firestore (Native, asia-south1)       9. IAM bindings scoped to the bucket / secret / repo
    5. closed Firestore rules (Firebase CLI) 10. budget alerts (with -Budgets): alerts only, not a spending cap

  Roles granted, nothing broader:
    qf-api        datastore.user (project), storage.objectAdmin (data bucket), secretAccessor (qf-ip-salt),
                  run.invoker on the worker job (granted by deploy-worker.ps1, once the job exists)
    qf-worker     datastore.user (project), storage.objectAdmin (data bucket), secretAccessor (qf-ip-salt)
    qf-scheduler  run.invoker on the API service (granted by scheduler.ps1)
    qf-build      artifactregistry.writer (repo), objectViewer (build staging bucket), logging.logWriter (project)
.EXAMPLE
  scripts\gcp\provision.ps1
  scripts\gcp\provision.ps1 -Budgets            # also create the US$5 and US$10 budget alerts
#>
param(
    [switch]$Budgets,
    [int[]]$BudgetAmounts = @(5, 10),
    [string]$BudgetCurrency = 'USD',
    [string]$Project = 'quantfolio-511108',
    [string]$Account = 'sahildeshmukh1616@gmail.com',
    [string]$Region = 'asia-south1'
)
. (Join-Path $PSScriptRoot '_gcp.ps1')
Assert-Identity

function Step([string]$Text) { Write-Host "`n== $Text" -ForegroundColor Cyan }

Step 'APIs'
$apis = 'run', 'cloudbuild', 'artifactregistry', 'firestore', 'cloudscheduler', 'secretmanager', 'billingbudgets',
'cloudbilling', 'iam', 'logging', 'storage'
$enabled = (Invoke-Gcloud services list --enabled '--format=value(config.name)' -Quiet) -split "`n"
$missing = @($apis | ForEach-Object { "$_.googleapis.com" } | Where-Object { $enabled -notcontains $_ })
if ($missing.Count) { Invoke-Gcloud services enable @missing | Out-Null; Write-Host "enabled: $($missing -join ', ')" } else { Write-Host 'all enabled' }

Step 'Service accounts'
$accounts = @{
    'qf-api'       = 'QuantFolio API (Cloud Run service)'
    'qf-worker'    = 'QuantFolio worker (Cloud Run job)'
    'qf-scheduler' = 'QuantFolio scheduled sweep (Cloud Scheduler OIDC)'
    'qf-build'     = 'QuantFolio image build (Cloud Build)'
}
foreach ($name in $accounts.Keys) {
    if (Test-GcloudResource iam service-accounts describe "$name@$SaDomain") { Write-Host "$name exists"; continue }
    Invoke-Gcloud iam service-accounts create $name "--display-name=$($accounts[$name])" | Out-Null
    Write-Host "$name created"
}

Step 'Artifact Registry'
if (-not (Test-GcloudResource artifacts repositories describe $Repo --location $Region)) {
    Invoke-Gcloud artifacts repositories create $Repo --repository-format=docker --location $Region '--description=QuantFolio API/worker images' | Out-Null
    Write-Host "repository $Repo created"
} else { Write-Host "repository $Repo exists" }
# Keep the 10 most recent images (rollback targets); delete older ones after 30 days. Never deletes a recent image.
Invoke-Gcloud artifacts repositories set-cleanup-policies $Repo --location $Region "--policy=$(Join-Path $DeployDir 'ar-cleanup.json')" --no-dry-run | Out-Null
Write-Host 'cleanup policy applied (keep 10 most recent; delete older than 30 days)'

Step 'Firestore'
if (-not (Test-GcloudResource firestore databases describe '--database=(default)')) {
    Invoke-Gcloud firestore databases create '--database=(default)' "--location=$Region" --type=firestore-native | Out-Null
    Write-Host "database (default) created in $Region"
} else { Write-Host 'database (default) exists' }
if (Get-Command firebase -ErrorAction SilentlyContinue) {
    Push-Location -LiteralPath $DeployDir
    try {
        $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        & firebase deploy --only firestore:rules --config firebase.data.json --project $Project --account $Account --non-interactive 2>&1 | Out-Host
        $ErrorActionPreference = $old
        if ($LASTEXITCODE -ne 0) { Write-Host 'Firestore rules were not deployed (see above). The API uses IAM; rules only close the browser SDK path.' -ForegroundColor Yellow }
    } finally { Pop-Location }
} else { Write-Host 'Firebase CLI missing: closed rules (deploy\firestore.rules) not deployed.' -ForegroundColor Yellow }

Step 'Firestore TTL (expire_at)'
$ttl = Invoke-Gcloud firestore fields ttls list '--format=value(name)' -Quiet
foreach ($group in 'runs', 'events', 'idempotency', 'cache', 'days', 'datasets') {
    if ($ttl -match "collectionGroups/$group/fields/expire_at") { Write-Host "$group ttl on"; continue }
    Invoke-Gcloud firestore fields ttls update expire_at "--collection-group=$group" --enable-ttl --async | Out-Null
    Write-Host "$group ttl requested"
}

Step 'Buckets'
$buckets = @(
    @{ Name = $Bucket; Days = 30 },
    @{ Name = "$Project-build"; Days = 7 }
)
foreach ($b in $buckets) {
    $uri = "gs://$($b.Name)"
    if (-not (Test-GcloudResource storage buckets describe $uri)) {
        Invoke-Gcloud storage buckets create $uri "--location=$Region" --uniform-bucket-level-access --public-access-prevention --default-storage-class=STANDARD | Out-Null
        Write-Host "$uri created"
    } else { Write-Host "$uri exists" }
    $rule = Join-Path $env:TEMP "qf-lifecycle-$($b.Days).json"
    ('{"rule":[{"action":{"type":"Delete"},"condition":{"age":' + $b.Days + '}}]}') | Set-Content -LiteralPath $rule -Encoding ascii
    Invoke-Gcloud storage buckets update $uri "--lifecycle-file=$rule" --public-access-prevention --uniform-bucket-level-access | Out-Null
    Remove-Item -LiteralPath $rule -Force
    Write-Host "$uri private; objects deleted after $($b.Days) days"
}

Step 'Secret qf-ip-salt'
if (-not (Test-GcloudResource secrets describe $SaltSecret)) {
    Invoke-Gcloud secrets create $SaltSecret --replication-policy=user-managed "--locations=$Region" | Out-Null
    Write-Host "$SaltSecret created"
}
$versions = Invoke-Gcloud secrets versions list $SaltSecret '--filter=state=ENABLED' '--format=value(name)' -Quiet
if (-not $versions) {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $file = Join-Path $env:TEMP ("qf-salt-" + [guid]::NewGuid().ToString('N'))
    try {
        [System.IO.File]::WriteAllText($file, [Convert]::ToBase64String($bytes))
        Invoke-Gcloud secrets versions add $SaltSecret "--data-file=$file" | Out-Null
    } finally {
        Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue
        [Array]::Clear($bytes, 0, $bytes.Length)
    }
    Write-Host 'random salt stored (value not shown)'
} else { Write-Host 'salt version exists (value not shown)' }

Step 'IAM'
foreach ($sa in $ApiSa, $WorkerSa) {
    Invoke-Gcloud projects add-iam-policy-binding $Project "--member=serviceAccount:$sa" --role=roles/datastore.user --condition=None '--format=none' | Out-Null
    Invoke-Gcloud storage buckets add-iam-policy-binding "gs://$Bucket" "--member=serviceAccount:$sa" --role=roles/storage.objectAdmin '--format=none' | Out-Null
    Invoke-Gcloud secrets add-iam-policy-binding $SaltSecret "--member=serviceAccount:$sa" --role=roles/secretmanager.secretAccessor '--format=none' | Out-Null
    Write-Host "$($sa): datastore.user, objectAdmin on gs://$Bucket, secretAccessor on $SaltSecret"
}
Invoke-Gcloud artifacts repositories add-iam-policy-binding $Repo --location $Region "--member=serviceAccount:$BuildSa" --role=roles/artifactregistry.writer '--format=none' | Out-Null
Invoke-Gcloud storage buckets add-iam-policy-binding "gs://$Project-build" "--member=serviceAccount:$BuildSa" --role=roles/storage.objectViewer '--format=none' | Out-Null
Invoke-Gcloud projects add-iam-policy-binding $Project "--member=serviceAccount:$BuildSa" --role=roles/logging.logWriter --condition=None '--format=none' | Out-Null
Write-Host "$($BuildSa): artifactregistry.writer on $Repo, objectViewer on gs://$Project-build, logging.logWriter"

if ($Budgets) {
    Step 'Budget alerts'
    $billing = Invoke-Gcloud billing projects describe $Project '--format=value(billingAccountName)' -Quiet
    if (-not $billing) { Stop-With "Billing is not enabled on $Project; budgets need a billing account (not changed by this script)." }
    $accountId = $billing -replace '^billingAccounts/', ''
    $existing = Invoke-Gcloud billing budgets list "--billing-account=$accountId" '--format=value(displayName)' -Quiet
    foreach ($amount in $BudgetAmounts) {
        $display = "QuantFolio $BudgetCurrency $amount"
        if ($existing -split "`n" -contains $display) { Write-Host "$display exists"; continue }
        Invoke-Gcloud billing budgets create "--billing-account=$accountId" "--display-name=$display" "--budget-amount=$amount$BudgetCurrency" `
            "--filter-projects=projects/$Project" '--threshold-rule=percent=0.5' '--threshold-rule=percent=0.9' '--threshold-rule=percent=1.0' `
            '--threshold-rule=percent=1.0,basis=forecasted-spend' | Out-Null
        Write-Host "$display created (e-mails billing admins at 50/90/100% and at 100% forecast; it does not stop spending)"
    }
}

Write-Host "`nProvisioning done. Next: scripts\gcp\build.ps1, deploy-worker.ps1, deploy-api.ps1, scheduler.ps1" -ForegroundColor Green
