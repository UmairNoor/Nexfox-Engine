# Supervises the NexFox worker process: restarts it with backoff if it exits, and rotates its log file.
# Started by the "NexFoxWorker" scheduled task (install-worker.ps1). Ctrl+C / task stop ends it cleanly.
$ErrorActionPreference = "Continue"
$Worker = Resolve-Path (Join-Path $PSScriptRoot "..\..\apps\worker")
$Py = Join-Path $Worker ".venv\Scripts\python.exe"
$LogDir = Join-Path $Worker "logs"
New-Item -ItemType Directory -Force $LogDir | Out-Null
$Log = Join-Path $LogDir "worker.log"
$delay = 5
while ($true) {
  if ((Test-Path $Log) -and ((Get-Item $Log).Length -gt 50MB)) {
    Move-Item -Force $Log (Join-Path $LogDir ("worker-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log"))
    Get-ChildItem $LogDir -Filter "worker-*.log" | Sort-Object LastWriteTime -Descending | Select-Object -Skip 10 | Remove-Item -Force
  }
  $started = Get-Date
  Push-Location $Worker
  & $Py -m nexfox_worker *>> $Log
  $code = $LASTEXITCODE
  Pop-Location
  if ($code -eq 2) { Add-Content $Log "worker configuration error (exit 2); retrying in 60 s"; Start-Sleep 60; continue }
  if (((Get-Date) - $started).TotalMinutes -gt 10) { $delay = 5 } else { $delay = [Math]::Min($delay * 2, 120) }
  Add-Content $Log "$(Get-Date -Format o) worker exited with code $code; restarting in $delay s"
  Start-Sleep $delay
}
