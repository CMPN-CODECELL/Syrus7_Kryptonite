<#
.SYNOPSIS
  Builds the web app in cloud mode and deploys it to Firebase Hosting.
.DESCRIPTION
  Without -Release the build goes to a preview channel: a temporary URL of its own, the live site is not touched.
  With -Release it becomes the live site. Only Hosting target "app" is deployed, never any other Firebase resource,
  and the project and account are always passed explicitly.

  The build is written to build\hosting (ignored by git), not to frontend\dist, so the local server on :8000 keeps
  serving the local build. Cloud mode is the Vite mode "cloud" (frontend\src\lib\cloud.ts); it needs no keys and
  the app has no sign-in. A successful build leaves the stamp build\hosting\.cloud-build (Hosting skips dot files),
  and -SkipBuild deploys only a build that carries it.

  Needs: the Firebase CLI (npm i -g firebase-tools) signed in as -Account (firebase login:add), the one-time setup
  in scripts\gcp\setup-firebase.ps1, and the Cloud Run service "quantfolio-api" (the /api rewrite points at it).
.EXAMPLE
  scripts\gcp\deploy-hosting.ps1                      # preview channel "preview", expires in 7 days
  scripts\gcp\deploy-hosting.ps1 -Channel pr12 -Expires 2d
  scripts\gcp\deploy-hosting.ps1 -Release -Message "v1.2"
#>
param(
    [string]$Channel = 'preview',
    [string]$Expires = '7d',
    [switch]$Release,          # deploy the live site instead of a preview channel
    [string]$Message = '',     # release note shown in the Hosting release history (with -Release)
    [switch]$SkipBuild,        # deploy build\hosting as it is
    [string]$Project = 'quantfolio-511108',
    [string]$Account = 'sahildeshmukh1616@gmail.com'
)
. (Join-Path (Split-Path -Parent $PSScriptRoot) '_common.ps1')
# The Firebase CLI and npm write progress to stderr; every step checks $LASTEXITCODE instead.
$ErrorActionPreference = 'Continue'

$Out = Join-Path $Root 'build\hosting'
$Scope = @('--project', $Project, '--account', $Account)

function Stop-With([string]$Text) {
    Write-Host $Text -ForegroundColor Red
    exit 1
}

if (-not (Get-Command firebase -ErrorAction SilentlyContinue)) { Stop-With 'The Firebase CLI is missing. Install it with: npm i -g firebase-tools' }
if ($Channel -notmatch '^[a-z0-9][a-z0-9-]{0,39}$' -or $Channel -eq 'live') { Stop-With 'The channel name must be lower-case letters, digits and dashes, and not "live". Use -Release for the live site.' }

Push-Location -LiteralPath $Root
try {
    $stamp = Join-Path $Out '.cloud-build'
    if (-not $SkipBuild) {
        Push-Location -LiteralPath $Frontend
        try {
            if (-not (Test-Path -LiteralPath 'node_modules')) { Stop-With 'frontend\node_modules is missing. Run scripts\setup.ps1 first.' }
            & npm run typecheck
            if ($LASTEXITCODE -ne 0) { Stop-With 'The type check failed; nothing was deployed.' }
            & npx vite build --mode cloud --outDir $Out --emptyOutDir
            if ($LASTEXITCODE -ne 0) { Stop-With 'The build failed; nothing was deployed.' }
            Set-Content -LiteralPath $stamp -Value 'vite build --mode cloud' -Encoding ascii
        } finally {
            Pop-Location
        }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Out 'index.html'))) { Stop-With "There is no build in $Out. Run without -SkipBuild." }
    # A build made another way is the local app (event streams, local wording): never publish that.
    if (-not (Test-Path -LiteralPath $stamp)) { Stop-With "The build in $Out was not made by this script in cloud mode. Run without -SkipBuild." }

    if ($Release) {
        Write-Host "Releasing to the LIVE site of $Project (Hosting target 'app')..." -ForegroundColor Yellow
        $extra = @()
        if ($Message) { $extra = @('--message', $Message) }
        & firebase deploy --only hosting:app @Scope @extra
    } else {
        Write-Host "Deploying to preview channel '$Channel' of $Project (the live site is not touched)..."
        & firebase hosting:channel:deploy $Channel --only app --expires $Expires --no-authorized-domains @Scope
    }
    if ($LASTEXITCODE -ne 0) { Stop-With 'The deploy failed. See the Firebase CLI output above.' }
} finally {
    Pop-Location
}
