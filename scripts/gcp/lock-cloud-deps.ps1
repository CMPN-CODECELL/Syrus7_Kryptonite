<#
.SYNOPSIS
  Regenerates backend\requirements.cloud.txt: the hashed, wheels-only Linux dependency set of the cloud image.
.DESCRIPTION
  Resolves backend\requirements.cloud.in for manylinux x86_64 / CPython 3.13 with `pip install --dry-run --report`
  (nothing is installed, the local venv is not touched) and writes every package with the sha256 of the exact wheel.
  Environment markers are evaluated on this machine, so the Dockerfile runs `pip check` on Linux to catch a
  dependency that only Linux needs; if it fails, add that package to requirements.cloud.in and run this again.
.EXAMPLE
  scripts\gcp\lock-cloud-deps.ps1
#>
param()
. (Join-Path (Split-Path -Parent $PSScriptRoot) '_common.ps1')
Assert-Venv

$work = Join-Path $env:TEMP ("qf-lock-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $work | Out-Null
try {
    $report = Join-Path $work 'report.json'
    & $Py -m pip install --dry-run --quiet --ignore-installed --report $report --target (Join-Path $work 't') `
        --platform manylinux2014_x86_64 --platform manylinux_2_28_x86_64 --python-version 3.13 --implementation cp `
        --only-binary=:all: -r (Join-Path $Backend 'requirements.cloud.in')
    if ($LASTEXITCODE -ne 0) { Write-Host 'Resolution failed.' -ForegroundColor Red; exit 1 }
    $code = @'
import json, sys
report, out = sys.argv[1], sys.argv[2]
rows = []
for item in json.load(open(report, encoding="utf-8"))["install"]:
    meta, info = item["metadata"], item["download_info"]
    digest = info["archive_info"]["hashes"]["sha256"]
    rows.append((meta["name"].lower(), f'{meta["name"]}=={meta["version"]} \\\n    --hash=sha256:{digest}'))
head = ("# Resolved runtime set for the Cloud Run image: Linux x86_64 (manylinux), CPython 3.13, wheels only.\n"
        "# Generated from requirements.cloud.in by scripts\\gcp\\lock-cloud-deps.ps1 (pip --dry-run --report, no install).\n"
        "# Installed with --require-hashes --only-binary=:all: in backend/Dockerfile. Do not edit by hand.\n\n")
open(out, "w", encoding="utf-8", newline="\n").write(head + "\n".join(r for _, r in sorted(rows)) + "\n")
print(f"{len(rows)} packages")
'@
    $script = Join-Path $work 'emit.py'
    Set-Content -LiteralPath $script -Value $code -Encoding utf8
    & $Py -I $script $report (Join-Path $Backend 'requirements.cloud.txt')
    if ($LASTEXITCODE -ne 0) { exit 1 }
    Write-Host 'backend\requirements.cloud.txt written.' -ForegroundColor Green
} finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
