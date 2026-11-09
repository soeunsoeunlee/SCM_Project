$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonExe = Join-Path $projectRoot 'backend\.venv\Scripts\python.exe'
$nextPath = Join-Path $projectRoot 'frontend\node_modules\next\dist\bin\next'
Get-CimInstance Win32_Process | Where-Object {
    ($_.ExecutablePath -eq $pythonExe -and $_.CommandLine -like '*uvicorn app.main:app*') -or
    ($_.CommandLine -and $_.CommandLine.Contains($nextPath))
} | ForEach-Object { Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue }
$pgCtl = Join-Path $projectRoot '.local\postgresql\pgsql\bin\pg_ctl.exe'
$pgData = Join-Path $projectRoot '.local\pgdata'
& $pgCtl -D $pgData status *> $null
if ($LASTEXITCODE -eq 0) { & $pgCtl -D $pgData -m fast -w stop }
Write-Host 'SCM servers stopped. Stored data was preserved.'
