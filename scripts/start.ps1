$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot
$pythonExe = Join-Path $projectRoot 'backend\.venv\Scripts\python.exe'
& $pythonExe scripts/setup-db.py
if ($LASTEXITCODE -ne 0) { throw 'Database startup failed.' }
foreach ($port in @(8000,3000)) {
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
        throw "Port $port is busy. Stop the existing server first (see README)."
    }
}
Start-Process -FilePath $pythonExe -ArgumentList '-m uvicorn app.main:app --host 127.0.0.1 --port 8000' -WorkingDirectory (Join-Path $projectRoot 'backend') -WindowStyle Hidden -RedirectStandardOutput '.local/api-out.log' -RedirectStandardError '.local/api-error.log'
$apiReady = $false
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    try {
        $null = Invoke-WebRequest http://127.0.0.1:8000/health -UseBasicParsing -TimeoutSec 2
        $apiReady = $true
        break
    } catch { Start-Sleep -Milliseconds 500 }
}
if (-not $apiReady) { throw 'API startup failed. Check .local/api-error.log.' }
$nodeExe = (Get-Command node.exe).Source
$nextPath = Join-Path $projectRoot 'frontend\node_modules\next\dist\bin\next'
Start-Process -FilePath $nodeExe -ArgumentList "`"$nextPath`" start --hostname 127.0.0.1 --port 3000" -WorkingDirectory (Join-Path $projectRoot 'frontend') -WindowStyle Hidden -RedirectStandardOutput '.local/web-out.log' -RedirectStandardError '.local/web-error.log'
$ready = $false
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    try {
        $null = Invoke-WebRequest http://127.0.0.1:3000/api/backend/api/auth/status -UseBasicParsing -TimeoutSec 2
        $ready = $true
        break
    } catch { Start-Sleep -Milliseconds 500 }
}
if (-not $ready) { throw 'Web startup failed. Check .local/web-error.log.' }
Write-Host 'SCM site ready: http://127.0.0.1:3000'
