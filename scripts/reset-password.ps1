$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$pythonExe = Join-Path $projectRoot 'backend\.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw 'Python environment is missing. Run the project setup first.'
}

# 한글 안내가 깨지지 않게 UTF-8 출력을 사용합니다.
$env:PYTHONUTF8 = '1'
& $pythonExe (Join-Path $projectRoot 'backend\reset_password.py')
if ($LASTEXITCODE -ne 0) {
    throw 'Password reset failed. Make sure the local PostgreSQL server is running.'
}
