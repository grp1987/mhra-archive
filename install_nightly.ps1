# Run once as Administrator, only after the initial R2 archive has finished.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$data = Join-Path $PSScriptRoot 'data'
$liveDb = Join-Path $data 'mhra.db'
$r2Index = Join-Path $data 'r2_archive.db'
New-Item -ItemType Directory -Force $data | Out-Null

if (-not (Test-Path $liveDb)) { Copy-Item (Join-Path $PSScriptRoot 'mhra.db') $liveDb }
if ((Test-Path (Join-Path $PSScriptRoot 'r2_archive.db')) -and -not (Test-Path $r2Index)) {
    Copy-Item (Join-Path $PSScriptRoot 'r2_archive.db') $r2Index
}

# Establish today's catalogue as the clean starting point. This avoids showing
# the gap since the bundled June catalogue as thousands of first-run changes.
$env:MHRA_DB_PATH = $liveDb
$env:R2_INDEX_PATH = $r2Index
Write-Host 'Creating current MHRA baseline and archiving anything newly published...' -ForegroundColor Cyan
& 'C:\Program Files\Python312\python.exe' -u nightly_update.py --baseline
if ($LASTEXITCODE -ne 0) { throw "Initial MHRA baseline failed (exit $LASTEXITCODE)" }

$nssm = 'C:\nssm\nssm.exe'
& $nssm stop THIL-MHRA | Out-Null
& $nssm set THIL-MHRA AppEnvironmentExtra @(
    'PORT=8091', 'HOST=127.0.0.1', 'PUBLIC_PREFIX=/THIL/mhra',
    "MHRA_DB_PATH=$liveDb", "R2_INDEX_PATH=$r2Index"
) | Out-Null
& $nssm start THIL-MHRA | Out-Null

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PSScriptRoot\run_nightly.ps1`"" `
    -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -Daily -At '02:00'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 8)
Register-ScheduledTask -TaskName 'THIL-MHRA-Nightly' -Action $action `
    -Trigger $trigger -Settings $settings -User 'SYSTEM' -RunLevel Highest -Force | Out-Null

Write-Host 'Nightly MHRA check installed for 02:00. Website restarted on persistent data.' -ForegroundColor Green
Write-Host "Live catalogue: $liveDb"
Write-Host "R2 register:    $r2Index"
Write-Host "Nightly log:    $(Join-Path $PSScriptRoot 'nightly.log')"
