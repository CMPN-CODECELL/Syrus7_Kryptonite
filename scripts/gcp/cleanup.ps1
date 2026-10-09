<#
.SYNOPSIS
  Deletes the cloud resources this deployment created (everything except the project, billing and Firebase itself).
.DESCRIPTION
  Lists what it would delete and does nothing unless -Execute is given. Order: scheduler, API service, worker job,
  budgets, buckets (with their objects), secret, Artifact Registry repo (all images), service accounts and their
  project bindings. Firestore data is deleted per collection only with -DeleteFirestoreData (the (default) database
  itself is kept: deleting it would also delete anything else the project keeps there). APIs stay enabled. The
  Firebase Hosting site is not touched (disable it with: firebase hosting:disable --project <p>).
.EXAMPLE
  scripts\gcp\cleanup.ps1                 # dry run: shows the list
  scripts\gcp\cleanup.ps1 -Execute
#>
param(
    [switch]$Execute,
    [switch]$DeleteFirestoreData,
    [string]$Project = 'quantfolio-511108',
    [string]$Account = 'sahildeshmukh1616@gmail.com',
    [string]$Region = 'asia-south1'
)
. (Join-Path $PSScriptRoot '_gcp.ps1')
Assert-Identity

$plan = @(
    "Cloud Scheduler job $SweepJob ($Region)",
    "Cloud Run service $ApiService and job $WorkerJob ($Region)",
    "Budgets named 'QuantFolio *' on the project's billing account",
    "Buckets gs://$Bucket and gs://$Project-build (all objects)",
    "Secret $SaltSecret",
    "Artifact Registry repository $Repo (all images)",
    "Service accounts qf-api, qf-worker, qf-scheduler, qf-build (and their project role bindings)"
)
if ($DeleteFirestoreData) { $plan += 'Firestore collections runs, idempotency, cache, days, users, control (recursive)' }
Write-Host "Cleanup of $Project would delete:" -ForegroundColor Yellow
$plan | ForEach-Object { Write-Host "  - $_" }
if (-not $Execute) { Write-Host 'Dry run. Add -Execute to delete.'; exit 0 }

Invoke-Gcloud scheduler jobs delete $SweepJob "--location=$Region" -AllowFail | Out-Null
Invoke-Gcloud run services delete $ApiService "--region=$Region" -AllowFail | Out-Null
Invoke-Gcloud run jobs delete $WorkerJob "--region=$Region" -AllowFail | Out-Null
$billing = Invoke-Gcloud billing projects describe $Project '--format=value(billingAccountName)' -AllowFail -Quiet
if ($billing) {
    $id = $billing -replace '^billingAccounts/', ''
    $budgets = Invoke-Gcloud billing budgets list "--billing-account=$id" '--format=value(name,displayName)' -AllowFail -Quiet
    foreach ($line in ($budgets -split "`n")) {
        $parts = $line -split "`t"
        if ($parts.Count -eq 2 -and $parts[1] -like 'QuantFolio *') { Invoke-Gcloud billing budgets delete $parts[0] -AllowFail | Out-Null }
    }
}
foreach ($b in "gs://$Bucket", "gs://$Project-build") { Invoke-Gcloud storage rm --recursive $b -AllowFail | Out-Null }
Invoke-Gcloud secrets delete $SaltSecret -AllowFail | Out-Null
Invoke-Gcloud artifacts repositories delete $Repo "--location=$Region" -AllowFail | Out-Null
if ($DeleteFirestoreData) {
    foreach ($c in 'runs', 'idempotency', 'cache', 'days', 'users', 'control') {
        Invoke-Gcloud firestore bulk-delete "--collection-ids=$c" -AllowFail | Out-Null
    }
}
foreach ($pair in @(@($ApiSa, 'roles/datastore.user'), @($WorkerSa, 'roles/datastore.user'), @($BuildSa, 'roles/logging.logWriter'))) {
    Invoke-Gcloud projects remove-iam-policy-binding $Project "--member=serviceAccount:$($pair[0])" "--role=$($pair[1])" --condition=None '--format=none' -AllowFail | Out-Null
}
foreach ($sa in $ApiSa, $WorkerSa, $SchedulerSa, $BuildSa) { Invoke-Gcloud iam service-accounts delete $sa -AllowFail | Out-Null }
Write-Host 'Cleanup finished. APIs remain enabled; billing, the project and Firebase Hosting were not changed.' -ForegroundColor Green
