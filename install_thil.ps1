# Install the THIL portal and its separately protected MHRA instance.
# Run in elevated PowerShell from C:\nyo-mhra after git pull.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$python = 'C:\Program Files\Python312\python.exe'
$nssm = 'C:\nssm\nssm.exe'
if (-not (Test-Path $python)) { throw "Python not found at $python" }
if (-not (Test-Path $nssm)) { throw "NSSM not found at $nssm" }

& $python -m pip install -q -r requirements.txt

$secretFile = Join-Path $PSScriptRoot 'thil_secret.txt'
if (-not (Test-Path $secretFile)) {
    $bytes = New-Object byte[] 48
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    [Convert]::ToBase64String($bytes) | Set-Content -NoNewline $secretFile
}
$secret = (Get-Content $secretFile -Raw).Trim()

function Install-ServiceSafe($name, $script, $environment) {
    $existing = Get-Service -Name $name -ErrorAction SilentlyContinue
    if ($existing) {
        & $nssm stop $name | Out-Null
        & $nssm remove $name confirm | Out-Null
    }
    & $nssm install $name $python $script
    & $nssm set $name AppDirectory $PSScriptRoot
    & $nssm set $name AppEnvironmentExtra $environment
    & $nssm set $name Start SERVICE_AUTO_START
    & $nssm set $name AppStdout (Join-Path $PSScriptRoot "$name.log")
    & $nssm set $name AppStderr (Join-Path $PSScriptRoot "$name.log")
    & $nssm start $name
}

Install-ServiceSafe 'THIL-Portal' 'serve_thil.py' @(
    "THIL_SECRET_KEY=$secret", 'THIL_COOKIE_SECURE=1', 'PORT=8095',
    'THIL_HUB_URL=https://www.grpsite.co.uk/THIL/Hub/'
)
Install-ServiceSafe 'THIL-MHRA' 'serve_vps.py' @(
    'PORT=8091', 'HOST=127.0.0.1', 'PUBLIC_PREFIX=/THIL/mhra'
)

Remove-Variable secret
Write-Host "THIL services installed. Next create the admin and update Caddy." -ForegroundColor Green
