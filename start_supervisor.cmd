@echo off
chcp 65001 >nul
setlocal

set "ROOT=%~dp0"
cd /d "%ROOT%"

echo ================================================
echo Finance crawler supervisor
echo Project: %CD%
echo ================================================
echo.
echo Starting main automation flow...
echo Close this window only when you want to stop the crawler.
echo.

if /I "%~1"=="--dry-run" (
    echo Dry run OK. Command would start:
    echo powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\run.ps1" -Task supervisor
    exit /b 0
)

set "PID_FILE=%TEMP%\finance_crawler_existing.pid"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$currentPid=$PID; $parentPid=(Get-CimInstance Win32_Process -Filter \"ProcessId=$PID\").ParentProcessId; $procs=Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.ProcessId -ne $currentPid -and $_.ProcessId -ne $parentPid -and ($_.CommandLine -match 'apps\.finance_crawler\.app' -or $_.CommandLine -match 'run\.ps1.*-Task\s+supervisor') }; if ($procs) { $procs[0].ProcessId }" > "%PID_FILE%"
set "EXISTING_PID="
set /p EXISTING_PID=<"%PID_FILE%"
del "%PID_FILE%" >nul 2>nul

if not "%EXISTING_PID%"=="" (
    echo A finance crawler process already appears to be running. PID: %EXISTING_PID%
    echo Stop the existing process first if you want to restart it.
    echo.
    echo Press any key to close this window.
    pause >nul
    exit /b 0
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\run.ps1" -Task supervisor
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo Supervisor exited with code %EXIT_CODE%.
echo Press any key to close this window.
pause >nul
exit /b %EXIT_CODE%
