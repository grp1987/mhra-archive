# Install a prepared THI Commercial Hub release as a private Windows service.
# Run as Administrator from C:\thi-commercial-hub after copying the release.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
$npx = (Get-Command npx.cmd -ErrorAction SilentlyContinue).Source
$nssm = 'C:\nssm\nssm.exe'
if (-not $npm -or -not $npx) { throw 'Node.js/npm is not installed or not on PATH.' }
if (-not (Test-Path $nssm)) { throw 'NSSM not found at C:\nssm\nssm.exe.' }

$dataDir = 'C:\THIL\data\hub'
$dbPath = Join-Path $dataDir 'thi-commercial-hub.sqlite'
New-Item -ItemType Directory -Force $dataDir | Out-Null

$env:HUB_BASE_PATH = '/THIL/Hub'
$env:PID_API_URL = 'http://127.0.0.1:8100'
$env:PID_BASE_PATH = '/THIL/PID'
$env:THI_DB_PATH = $dbPath

& $npm ci
if ($LASTEXITCODE -ne 0) { throw "npm ci failed (exit $LASTEXITCODE)" }
& $npm run build
if ($LASTEXITCODE -ne 0) { throw "Hub production build failed (exit $LASTEXITCODE)" }

$service = 'THIL-Commercial-Hub'
if (Get-Service $service -ErrorAction SilentlyContinue) {
    & $nssm stop $service | Out-Null
    & $nssm remove $service confirm | Out-Null
}
& $nssm install $service $npx 'next start -H 127.0.0.1 -p 8200'
& $nssm set $service AppDirectory $PSScriptRoot
& $nssm set $service AppEnvironmentExtra @(
    'HUB_BASE_PATH=/THIL/Hub', 'PID_API_URL=http://127.0.0.1:8100',
    'PID_BASE_PATH=/THIL/PID', "THI_DB_PATH=$dbPath"
)
& $nssm set $service Start SERVICE_AUTO_START
& $nssm set $service AppStdout (Join-Path $PSScriptRoot 'hub-service.log')
& $nssm set $service AppStderr (Join-Path $PSScriptRoot 'hub-service.log')
& $nssm start $service
Start-Sleep 5
& $nssm status $service
Write-Host 'Commercial Hub installed privately on 127.0.0.1:8200.' -ForegroundColor Green
