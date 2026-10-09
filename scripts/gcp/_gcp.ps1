# Shared names and helpers for the scripts\gcp\*.ps1 deployment scripts (dot-sourced). Windows PowerShell 5.1.
# Every gcloud call passes --project and --account explicitly, so the active gcloud configuration never matters
# and no other project can be touched. Nothing here prints tokens or secret values.
. (Join-Path (Split-Path -Parent $PSScriptRoot) '_common.ps1')
$ErrorActionPreference = 'Stop'

if (-not $Project) { $Project = 'quantfolio-511108' }
if (-not $Account) { $Account = 'sahildeshmukh1616@gmail.com' }
if (-not $Region) { $Region = 'asia-south1' }

$Repo = 'quantfolio'
$ImageBase = "$Region-docker.pkg.dev/$Project/$Repo/app"
$Bucket = "$Project-data"
$ApiService = 'quantfolio-api'
$WorkerJob = 'quantfolio-worker'
$SweepJob = 'quantfolio-sweep'
$SaltSecret = 'qf-ip-salt'
$SaDomain = "$Project.iam.gserviceaccount.com"
$ApiSa = "qf-api@$SaDomain"
$WorkerSa = "qf-worker@$SaDomain"
$SchedulerSa = "qf-scheduler@$SaDomain"
$BuildSa = "qf-build@$SaDomain"
$DeployDir = Join-Path $Root 'deploy'
$RecordFile = Join-Path $DeployDir 'last-deploy.json'   # image digest, revision, job config (no secrets)

function Stop-With([string]$Text) {
    Write-Host $Text -ForegroundColor Red
    exit 1
}

# gcloud with the project and account pinned. Returns stdout as text; stderr (progress) goes to the console.
# Throws when gcloud fails unless -AllowFail is given (then $LASTEXITCODE tells).
function Invoke-Gcloud {
    param([Parameter(ValueFromRemainingArguments = $true)] [string[]]$GArgs, [switch]$AllowFail, [switch]$Quiet)
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & gcloud @GArgs --project $Project --account $Account --quiet 2>&1
    } finally {
        $ErrorActionPreference = $old
    }
    $stdout = @($out | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] }) -join "`n"
    $stderr = @($out | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] } | ForEach-Object { $_.ToString() }) -join "`n"
    if ($LASTEXITCODE -ne 0) {
        if ($AllowFail) { return $null }
        if ($stderr) { Write-Host $stderr -ForegroundColor DarkYellow }
        throw "gcloud $($GArgs -join ' ') failed (exit $LASTEXITCODE)"
    }
    if ($stderr -and -not $Quiet) { Write-Host $stderr -ForegroundColor DarkGray }
    return $stdout
}

function Test-GcloudResource {
    param([Parameter(ValueFromRemainingArguments = $true)] [string[]]$GArgs)
    $null = Invoke-Gcloud @GArgs '--format=value(name)' -AllowFail -Quiet
    return ($LASTEXITCODE -eq 0)
}

function Assert-Identity {
    if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) { Stop-With 'The Google Cloud CLI (gcloud) is missing.' }
    $accounts = & gcloud auth list --format='value(account)' 2>$null
    if (@($accounts) -notcontains $Account) { Stop-With "gcloud has no credentials for $Account. Run: gcloud auth login $Account" }
    $id = Invoke-Gcloud projects describe $Project --format='value(projectId)' -Quiet
    if ($id -ne $Project) { Stop-With "Project $Project is not visible to $Account." }
}

function Save-Record([hashtable]$Values) {
    $data = @{}
    if (Test-Path -LiteralPath $RecordFile) {
        (Get-Content -LiteralPath $RecordFile -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $data[$_.Name] = $_.Value }
    }
    foreach ($k in $Values.Keys) { $data[$k] = $Values[$k] }
    $data['updated_utc'] = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    ($data | ConvertTo-Json -Depth 6) | Set-Content -LiteralPath $RecordFile -Encoding utf8
}

function Get-Record([string]$Key) {
    if (-not (Test-Path -LiteralPath $RecordFile)) { return $null }
    return (Get-Content -LiteralPath $RecordFile -Raw | ConvertFrom-Json).$Key
}
