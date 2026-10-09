<#
.SYNOPSIS
  One-time Firebase setup for the hosted demo: the Hosting site and the "app" deploy target.
.DESCRIPTION
  Safe to run again: every step first checks what exists. It never deploys anything and never touches billing.

  If the command line cannot add Firebase to the project, a person has to do it once in a browser, because Google
  asks for consent there: https://console.firebase.google.com -> "Add project" -> pick the existing Google Cloud
  project (quantfolio-511108) and accept the Firebase terms. The app has no sign-in, so Authentication stays off.

  Needs the Firebase CLI (npm i -g firebase-tools) signed in as -Account.
.EXAMPLE
  scripts\gcp\setup-firebase.ps1
#>
param(
    # Hosting site id. "quantfolio" is reserved by another Firebase project, so the project's default site is used.
    # Another id is created when it is free; when it is taken the default site is the fallback.
    [string]$Site = 'quantfolio-511108',
    [string]$Project = 'quantfolio-511108',
    [string]$Account = 'sahildeshmukh1616@gmail.com'
)
. (Join-Path (Split-Path -Parent $PSScriptRoot) '_common.ps1')

$Scope = @('--project', $Project, '--account', $Account)

function Stop-With([string]$Text) {
    Write-Host $Text -ForegroundColor Red
    exit 1
}

# Firebase CLI output as text. Failures are reported through $LASTEXITCODE, not thrown.
function Invoke-Firebase {
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { return (& firebase @args 2>&1 | Out-String) } finally { $ErrorActionPreference = $old }
}

if (-not (Get-Command firebase -ErrorAction SilentlyContinue)) { Stop-With 'The Firebase CLI is missing. Install it with: npm i -g firebase-tools' }

Push-Location -LiteralPath $Root
try {
    # 1. Firebase on the project.
    $projects = Invoke-Firebase projects:list --account $Account
    if ($projects -notmatch [regex]::Escape($Project)) {
        Write-Host "Adding Firebase to $Project..."
        $added = Invoke-Firebase projects:addfirebase $Project --account $Account
        if ($LASTEXITCODE -ne 0) {
            Write-Host $added
            Stop-With "Firebase could not be added from the command line. Open https://console.firebase.google.com as $Account, choose 'Add project', pick $Project, accept the terms, then run this script again."
        }
    }
    Write-Host "Firebase is on $Project."

    # 2. Hosting site. Site ids are global, so the wanted one may belong to someone else.
    $sites = Invoke-Firebase hosting:sites:list @Scope
    if ($LASTEXITCODE -ne 0) { Write-Host $sites; Stop-With 'Could not list the Hosting sites.' }
    if ($sites -notmatch "(?<![\w-])$([regex]::Escape($Site))(?![\w-])") {
        $created = Invoke-Firebase hosting:sites:create $Site @Scope
        if ($LASTEXITCODE -ne 0) {
            Write-Host $created
            Write-Host "Site id '$Site' is not available; using the project's default site '$Project' instead." -ForegroundColor Yellow
            $Site = $Project
        }
    }
    $applied = Invoke-Firebase target:apply hosting app $Site @Scope
    if ($LASTEXITCODE -ne 0) { Write-Host $applied; Stop-With 'Could not bind the deploy target "app".' }
    Write-Host "Hosting site: https://$Site.web.app (deploy target 'app' in .firebaserc)."

    Write-Host 'Done. Next: scripts\gcp\deploy-hosting.ps1 (preview channel).' -ForegroundColor Green
} finally {
    Pop-Location
}
