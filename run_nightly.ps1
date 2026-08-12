$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$python = 'C:\Program Files\Python312\python.exe'
$log = Join-Path $PSScriptRoot 'nightly.log'
$env:MHRA_DB_PATH = Join-Path $PSScriptRoot 'data\mhra.db'
$env:R2_INDEX_PATH = Join-Path $PSScriptRoot 'data\r2_archive.db'

"`n=== $(Get-Date -Format o) nightly update started ===" | Add-Content $log
try {
    & $python -u nightly_update.py *>> $log
    if ($LASTEXITCODE -ne 0) { throw "Nightly updater exited with code $LASTEXITCODE" }
    "=== $(Get-Date -Format o) nightly update completed ===" | Add-Content $log
} catch {
    "=== $(Get-Date -Format o) nightly update FAILED: $_ ===" | Add-Content $log
    throw
}
