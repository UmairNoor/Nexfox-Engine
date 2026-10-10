<#
.SYNOPSIS
  Local development on Windows without Docker: API (SQLite, mock billing), web (Next dev) and a worker on the
  SIMULATED MT5 backend, each in its own window. Not for production.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File deploy\windows\dev-start.ps1
#>
$Repo = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$Api = Join-Path $Repo "apps\api"
$Web = Join-Path $Repo "apps\web"
$Worker = Join-Path $Repo "apps\worker"

if (-not (Test-Path "$Api\.venv")) { py -3 -m venv "$Api\.venv"; & "$Api\.venv\Scripts\python.exe" -m pip install -r "$Api\requirements.txt" }
if (-not (Test-Path "$Worker\.venv")) { py -3 -m venv "$Worker\.venv"; & "$Worker\.venv\Scripts\python.exe" -m pip install -r "$Worker\requirements.txt" }
if (-not (Test-Path "$Web\node_modules")) { Push-Location $Web; npm ci; Pop-Location }

$env:ENV = "dev"
& "$Api\.venv\Scripts\python.exe" -c "import sys; sys.path.insert(0, r'$Api'); import os; os.chdir(r'$Api'); from app.db import migrate_to_head; migrate_to_head()"
Push-Location $Api
$token = (& "$Api\.venv\Scripts\python.exe" -m app.cli create-worker dev-worker 2) | Select-String "WORKER_TOKEN" | ForEach-Object { ($_ -split ": ")[1] }
Pop-Location
if ($token) { Set-Content -Path "$Worker\.dev-token" -Value $token -Encoding ascii }
$token = Get-Content "$Worker\.dev-token" -ErrorAction SilentlyContinue

Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$Api'; .\.venv\Scripts\uvicorn.exe app.main:app --reload --port 8000"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$Web'; npm run dev"
$fake = Join-Path $Worker "dev-fake-mt5.json"
# WriteAllText without an encoding argument writes UTF-8 without a BOM (Set-Content -Encoding utf8 adds one in PS 5.1)
if (-not (Test-Path $fake)) { [IO.File]::WriteAllText($fake, '{"accounts": {"1000001": {"password": "demo-pass", "server": "Fake-Demo", "trade_mode": "demo", "balance": 10000}}, "seed": 7}') }
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$Worker'; `$env:MT5_BACKEND='fake'; `$env:FAKE_MT5_CONFIG='$fake'; `$env:WORKER_TOKEN='$token'; `$env:NEXFOX_API_URL='http://127.0.0.1:8000'; `$env:LOG_JSON='false'; .\.venv\Scripts\python.exe -m nexfox_worker"
Write-Host "API http://localhost:8000/docs   Web http://localhost:3000   Simulated MT5 account: login 1000001 / server Fake-Demo / password demo-pass"
