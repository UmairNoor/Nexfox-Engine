<#
.SYNOPSIS
  Installs the NexFox Engine MT5 worker on a Windows host (VPS) without Docker.
.DESCRIPTION
  - creates a Python virtual environment for apps\worker and installs its requirements (incl. MetaTrader5)
  - creates apps\worker\.env from the example if missing (fill WORKER_TOKEN, NEXFOX_API_URL, MT5_TERMINALS)
  - registers a Scheduled Task "NexFoxWorker" that starts at logon of this user and restarts on failure.
    MT5 terminals need an interactive desktop session, so the worker runs in the logged-in user's session
    (enable auto-logon on the VPS) instead of as a session-0 service.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File deploy\windows\install-worker.ps1
  powershell -ExecutionPolicy Bypass -File deploy\windows\install-worker.ps1 -Python "C:\Python312\python.exe"
#>
param(
  [string]$Python = "py",
  [string]$TaskName = "NexFoxWorker"
)
$ErrorActionPreference = "Stop"
$Repo = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$Worker = Join-Path $Repo "apps\worker"

Write-Host "==> virtual environment"
if (-not (Test-Path (Join-Path $Worker ".venv"))) {
  if ($Python -eq "py") { & py -3 -m venv (Join-Path $Worker ".venv") } else { & $Python -m venv (Join-Path $Worker ".venv") }
}
$Py = Join-Path $Worker ".venv\Scripts\python.exe"
& $Py -m pip install --upgrade pip | Out-Null
& $Py -m pip install -r (Join-Path $Worker "requirements.txt")

Write-Host "==> configuration"
$EnvFile = Join-Path $Worker ".env"
if (-not (Test-Path $EnvFile)) {
  Copy-Item (Join-Path $Worker ".env.example") $EnvFile
  Write-Host "    created $EnvFile - edit WORKER_TOKEN, NEXFOX_API_URL and MT5_TERMINALS, then rerun this script"
}
# restrict the .env (holds the worker token) to the current user and SYSTEM
icacls $EnvFile /inheritance:r /grant:r "$($env:USERNAME):(R,W)" "SYSTEM:(F)" | Out-Null

Write-Host "==> connectivity check"
Push-Location $Worker
try { & $Py -m nexfox_worker --check; $ok = ($LASTEXITCODE -eq 0) } finally { Pop-Location }
if (-not $ok) { Write-Warning "Check failed. Fix .env and rerun. The scheduled task is registered anyway." }

Write-Host "==> scheduled task $TaskName"
$Runner = Join-Path $PSScriptRoot "run-worker.ps1"
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Runner`"" -WorkingDirectory $Worker
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Force | Out-Null
Write-Host "Installed. Start now with:  Start-ScheduledTask -TaskName $TaskName"
Write-Host "Logs: $Worker\logs\worker.log    Stop: Stop-ScheduledTask -TaskName $TaskName (bots stop; positions keep their SL/TP)"
