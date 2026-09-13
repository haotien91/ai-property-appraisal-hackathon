param([int]$Port = 8124)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$runtime = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $runtime)) {
    $runtime = Join-Path (Split-Path $PSScriptRoot -Parent) '.venv\Scripts\python.exe'
}
$runtimeReady = $false

if (Test-Path -LiteralPath $runtime) {
    try {
        & $runtime -c "import sys" *> $null
        $runtimeReady = ($LASTEXITCODE -eq 0)
    }
    catch {
        $runtimeReady = $false
    }
}

if (-not $runtimeReady) {
    throw @'
The project Python environment (.venv) is missing or broken.
Install Python 3.11 or newer, open PowerShell in this project directory, and run:

  py -m venv --clear .venv
  .\.venv\Scripts\python.exe -m pip install -r .\backend\requirements-local-app.txt
  powershell -ExecutionPolicy Bypass -File .\start-app.ps1
'@
}

Write-Host "Starting the appraisal app at http://127.0.0.1:$Port/index.html"
& $runtime -B scripts/serve_app.py --port $Port
