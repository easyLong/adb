@echo off
chcp 65001 >nul
setlocal

set "ROOT=%~dp0"
cd /d "%ROOT%"

echo ================================================
echo Finance crawler detail backfill
echo Project: %CD%
echo ================================================
echo.
echo This tool backfills Tencent Docs detail tasks by date.
echo Example input: 2026-05-26,2026-05-27
echo.

set "BACKFILL_DATES="
set "DRY_RUN=0"
set "INTERACTIVE=1"

:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="--dry-run" (
    set "DRY_RUN=1"
) else (
    if "%BACKFILL_DATES%"=="" (
        set "BACKFILL_DATES=%~1"
        set "INTERACTIVE=0"
    ) else (
        set "BACKFILL_DATES=%BACKFILL_DATES%,%~1"
    )
)
shift
goto parse_args
:args_done

set "EXISTING_PID="
for /f "delims=" %%P in ('powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$currentPid=$PID; $procs=Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.ProcessId -ne $currentPid -and ($_.CommandLine -match 'apps\.finance_crawler\.app' -or $_.CommandLine -match 'run\.ps1') }; if ($procs) { $procs[0].ProcessId }"') do (
    if "%EXISTING_PID%"=="" set "EXISTING_PID=%%P"
)

if not "%EXISTING_PID%"=="" if "%DRY_RUN%"=="0" (
    echo A finance crawler process already appears to be running. PID: %EXISTING_PID%
    echo Backfill uses the same phone and task queue as the main flow.
    choice /C YN /N /M "Continue anyway? [Y/N] "
    if errorlevel 2 (
        echo Backfill cancelled.
        if "%INTERACTIVE%"=="1" (
            echo Press any key to close this window.
            pause >nul
        )
        exit /b 0
    )
    echo.
)

if "%INTERACTIVE%"=="1" (
    set /p "BACKFILL_DATES=Enter date(s): "
)

if "%BACKFILL_DATES%"=="" (
    echo No date provided.
    if "%INTERACTIVE%"=="1" (
        echo Press any key to close this window.
        pause >nul
    )
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop';" ^
  "$root='%ROOT%';" ^
  "$raw='%BACKFILL_DATES%';" ^
  "$dates=$raw -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ };" ^
  "if (-not $dates) { throw 'No valid date provided.' }" ^
  "foreach ($d in $dates) { if ($d -notmatch '^\d{4}-\d{2}-\d{2}$') { throw ('Invalid date: ' + $d + '. Expected yyyy-MM-dd.') } }" ^
  "Write-Host ('Backfill dates: ' + ($dates -join ', '));" ^
  "if ('%DRY_RUN%' -eq '1') { Write-Host 'Dry run OK. No task was executed.'; exit 0 }" ^
  "foreach ($d in $dates) {" ^
  "  Write-Host ''; Write-Host ('[fetch] ' + $d);" ^
  "  $env:TENCENT_DOC_SCAN_MODE='date';" ^
  "  $env:TENCENT_DOC_SCAN_DATE=$d;" ^
  "  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'scripts\run.ps1') -Task fetch;" ^
  "  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }" ^
  "}" ^
  "Remove-Item Env:TENCENT_DOC_SCAN_MODE -ErrorAction SilentlyContinue;" ^
  "Remove-Item Env:TENCENT_DOC_SCAN_DATE -ErrorAction SilentlyContinue;" ^
  "Write-Host ''; Write-Host '[detail] start';" ^
  "$env:DETAIL_SOURCE_DATES=($dates -join ',');" ^
  "& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'scripts\run.ps1') -Task detail;" ^
  "Remove-Item Env:DETAIL_SOURCE_DATES -ErrorAction SilentlyContinue;" ^
  "exit $LASTEXITCODE"

set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo Backfill exited with code %EXIT_CODE%.
if "%INTERACTIVE%"=="1" (
    echo Press any key to close this window.
    pause >nul
)
exit /b %EXIT_CODE%
