# MHRA Archive - VPS setup + serve (bare IP + passcode). Run in an ELEVATED PowerShell.
# Idempotent: installs deps, opens the firewall port, registers a low-priority
# scheduled task that keeps the server running, and starts it.
#
#   1) put your passcode in  secret_pw.txt  (one line, this folder) - it is gitignored
#   2) run:  .\run_vps.ps1
#
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$Port = 8090
$Task = 'MHRA-Archive'

# --- passcode (kept out of git) ---
$pwFile = Join-Path $PSScriptRoot 'secret_pw.txt'
if (-not (Test-Path $pwFile)) {
    Write-Host "Create secret_pw.txt (one line = your passcode) in this folder first." -ForegroundColor Red
    exit 1
}
$pw = (Get-Content $pwFile -Raw).Trim()
if (-not $pw) { Write-Host "secret_pw.txt is empty." -ForegroundColor Red; exit 1 }

# --- python (resolve to a FULL path so a service running as LocalSystem finds it) ---
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = "C:\Program Files\Python312\python.exe" }
if (-not (Test-Path $py)) { Write-Host "python not found - install Python or fix PATH." -ForegroundColor Red; exit 1 }
Write-Host "using python: $py"
Write-Host "--- installing deps ---"
& $py -m pip install -q -r (Join-Path $PSScriptRoot 'requirements.txt')

# --- self-signed TLS cert (so the passcode is encrypted on the bare IP) ---
try { $ip = (Invoke-RestMethod -Uri 'https://api.ipify.org' -TimeoutSec 8).Trim() }
catch { $ip = 'localhost' }
Write-Host "--- generating self-signed cert for $ip ---"
& $py (Join-Path $PSScriptRoot 'gen_cert.py') $ip

# --- firewall (idempotent) ---
if (-not (Get-NetFirewallRule -DisplayName "MHRA Archive $Port" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "MHRA Archive $Port" -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort $Port | Out-Null
    Write-Host "opened firewall TCP $Port" -ForegroundColor Green
}

# --- install as an NSSM Windows service (same mechanism as Golfsito/ORO) ---
# NSSM is more robust than a scheduled task: full python path, its own env, auto
# restart, BelowNormal priority, survives logout/reboot.
$nssm = "C:\nssm\nssm.exe"
if (-not (Test-Path $nssm)) { $nssm = (Get-Command nssm -ErrorAction SilentlyContinue).Source }
if (-not $nssm) { Write-Host "nssm.exe not found (expected C:\nssm\nssm.exe)." -ForegroundColor Red; exit 1 }

# clean up any earlier scheduled-task attempt
Stop-ScheduledTask   -TaskName $Task -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $Task -Confirm:$false -ErrorAction SilentlyContinue
& $nssm stop $Task 2>$null; & $nssm remove $Task confirm 2>$null

& $nssm install $Task $py "serve_vps.py"
& $nssm set $Task AppDirectory $PSScriptRoot
& $nssm set $Task AppEnvironmentExtra "MHRA_PW=$pw" "PORT=$Port" "HOST=0.0.0.0"
& $nssm set $Task Start SERVICE_AUTO_START
& $nssm set $Task AppPriority BELOW_NORMAL_PRIORITY_CLASS
& $nssm set $Task AppStdout (Join-Path $PSScriptRoot 'service.log')
& $nssm set $Task AppStderr (Join-Path $PSScriptRoot 'service.log')
& $nssm start $Task
Start-Sleep 3

Write-Host "`nServing on  https://${ip}:$Port   (TLS self-signed + passcode gate ON)" -ForegroundColor Cyan
Write-Host "(browser warns 'not trusted' once for the self-signed cert - click through; connection is encrypted)" -ForegroundColor DarkGray
Write-Host "Status:  & '$nssm' status $Task    Logs: service.log"
Write-Host "Stop:    & '$nssm' stop $Task       Remove: & '$nssm' remove $Task confirm"
Write-Host "Update:  git pull ; & '$nssm' restart $Task"
