@echo off
REM Standalone run of Rehman Bot (outside the NexFox Engine platform), for development on a demo account.
REM On the platform the bot is run by the MT5 worker (apps\worker) with per-instance config and safety checks.
REM Set credentials in the environment first, e.g.:
REM   set REHMAN_MT5_LOGIN=12345678
REM   set REHMAN_MT5_PASSWORD=...
REM   set REHMAN_MT5_SERVER=XMGlobal-MT5 6
REM Real-money accounts are refused unless REHMAN_ALLOW_LIVE=true.
echo ==========================================
echo   Rehman Bot - XAUUSD Liquidity + Sweep
echo ==========================================
echo.

cd /d "%~dp0"

if "%REHMAN_MT5_LOGIN%"=="" (
    echo REHMAN_MT5_LOGIN is not set. See the comments at the top of this file.
    exit /b 1
)

python simple_bot.py

if %errorlevel% neq 0 (
    echo.
    echo Bot exited with error %errorlevel%.
    exit /b %errorlevel%
)

echo Bot stopped normally.
