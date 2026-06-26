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

# --- python + deps ---
$py = "C:\Program Files\Python312\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
Write-Host "--- installing deps ---"
& $py -m pip install -q -r (Join-Path $PSScriptRoot 'requirements.txt')

# --- firewall (idempotent) ---
if (-not (Get-NetFirewallRule -DisplayName "MHRA Archive $Port" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "MHRA Archive $Port" -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort $Port | Out-Null
    Write-Host "opened firewall TCP $Port" -ForegroundColor Green
}

# --- scheduled task: runs at startup, BelowNormal priority, restarts on failure ---
$action  = New-ScheduledTaskAction -Execute $py -Argument 'serve_vps.py' -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtStartup
# BelowNormal priority (7) so it never competes with ORO; env passes port + passcode
$settings = New-ScheduledTaskSettingsSet -Priority 7 -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$env:MHRA_PW = $pw; $env:PORT = "$Port"; $env:HOST = '0.0.0.0'

Unregister-ScheduledTask -TaskName $Task -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $Task -Action $action -Trigger $trigger -Settings $settings `
    -RunLevel Highest -User 'SYSTEM' -Force | Out-Null
Write-Host "registered scheduled task '$Task' (starts at boot, BelowNormal priority)" -ForegroundColor Green

# the scheduled task runs under SYSTEM which won't see this shell's env vars, so
# write a tiny launcher the task actually executes, carrying the passcode/port.
$launcher = Join-Path $PSScriptRoot '_task_launch.cmd'
"@echo off`r`nset MHRA_PW=$pw`r`nset PORT=$Port`r`nset HOST=0.0.0.0`r`ncd /d `"$PSScriptRoot`"`r`n`"$py`" serve_vps.py" |
    Set-Content -Path $launcher -Encoding ASCII
$action2 = New-ScheduledTaskAction -Execute $launcher -WorkingDirectory $PSScriptRoot
Set-ScheduledTask -TaskName $Task -Action $action2 | Out-Null

Start-ScheduledTask -TaskName $Task
Start-Sleep 3
Write-Host "`nServing on  http://<this-VPS-public-IP>:$Port   (passcode gate ON)" -ForegroundColor Cyan
Write-Host "Stop with:   Stop-ScheduledTask -TaskName $Task ; Unregister-ScheduledTask -TaskName $Task -Confirm:`$false"
Write-Host "Update DB:   git pull  (then)  Restart-ScheduledTask -TaskName $Task"
