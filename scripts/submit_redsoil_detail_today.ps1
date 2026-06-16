param(
    [ValidateSet("run", "install", "status", "start", "uninstall")]
    [string]$Action = "run",
    [string]$TaskName = "ADB Redsoil Detail Today Submit",
    [string]$Time = "16:00",
    [string]$Python = "python",
    [string]$EnvFile = ".env",
    [ValidateSet("today", "yesterday")]
    [string]$TargetMode = "today",
    [int]$Limit = 15,
    [string]$TriggerType = "scheduled_today_detail",
    [int]$MaxRetries = 3,
    [int]$RetryDelaySeconds = 30
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$LogDir = Join-Path $Root "apps\finance_crawler\logs\scheduled_tasks"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Get-TaskStatusText {
    param([string]$Name)

    $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "scheduled task not found: $Name"
        return
    }

    $info = Get-ScheduledTaskInfo -TaskName $Name
    Write-Host "task: $Name"
    Write-Host "state: $($task.State)"
    Write-Host "last_run_time: $($info.LastRunTime)"
    Write-Host "last_task_result: $($info.LastTaskResult)"
    Write-Host "next_run_time: $($info.NextRunTime)"
    Write-Host "actions:"
    foreach ($taskAction in $task.Actions) {
        Write-Host "  $($taskAction.Execute) $($taskAction.Arguments)"
    }
    Write-Host "triggers:"
    foreach ($trigger in $task.Triggers) {
        Write-Host "  $($trigger.StartBoundary) enabled=$($trigger.Enabled)"
    }
}

if ($Action -eq "install") {
    $SubmitScript = $PSCommandPath
    $TaskAction = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$SubmitScript`" -Action run -Python `"$Python`" -EnvFile `"$EnvFile`" -TargetMode $TargetMode -Limit $Limit -TriggerType $TriggerType -MaxRetries $MaxRetries -RetryDelaySeconds $RetryDelaySeconds" `
        -WorkingDirectory $Root

    $Trigger = New-ScheduledTaskTrigger -Daily -At $Time
    $Settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $TaskAction `
        -Trigger $Trigger `
        -Settings $Settings `
        -Description "Submit today's redsoil_detail tasks into crawler_app.task_submissions." `
        -Force | Out-Null

    Write-Host "registered task: $TaskName"
    Write-Host "time: $Time"
    Write-Host "target_mode: $TargetMode"
    Write-Host "limit: $Limit"
    Write-Host "script: $SubmitScript"
    exit 0
}

if ($Action -eq "status") {
    Get-TaskStatusText -Name $TaskName
    exit 0
}

if ($Action -eq "start") {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        throw "scheduled task not found: $TaskName. Run with -Action install first."
    }
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "started scheduled task: $TaskName"
    Get-TaskStatusText -Name $TaskName
    exit 0
}

if ($Action -eq "uninstall") {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "unregistered task: $TaskName"
    } else {
        Write-Host "scheduled task not found: $TaskName"
    }
    exit 0
}

function Load-ProjectEnv {
    param([string]$Path)
    $ResolvedPath = if ([System.IO.Path]::IsPathRooted($Path)) { $Path } else { Join-Path $Root $Path }
    if (-not (Test-Path -LiteralPath $ResolvedPath)) {
        return
    }

    foreach ($line in Get-Content -LiteralPath $ResolvedPath) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }
        if ($trimmed -match '^\s*(?:\$env:)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') {
            $name = $matches[1]
            $value = $matches[2].Trim().Trim('"').Trim("'")
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

Load-ProjectEnv $EnvFile

$env:REDSOIL_DETAIL_TARGET_MODE = $TargetMode
$env:REDSOIL_DETAIL_LIMIT = [string]$Limit
$env:REDSOIL_DETAIL_TRIGGER_TYPE = $TriggerType

$LogPath = Join-Path $LogDir ("redsoil_detail_today_{0}.log" -f (Get-Date -Format "yyyyMMdd"))
$PythonCode = @'
import os
from datetime import date, timedelta

from apps.finance_crawler.storage.db import init_db
from apps.finance_crawler.services.runtime_config import load_runtime_config
from apps.finance_crawler.crawler_app.workflows.submit_triggers import (
    submit_document_trigger_config,
)

target_mode = os.environ.get("REDSOIL_DETAIL_TARGET_MODE", "today").strip().lower()
if target_mode == "yesterday":
    target_date = date.today() - timedelta(days=1)
else:
    target_date = date.today()

trigger_type = os.environ.get("REDSOIL_DETAIL_TRIGGER_TYPE", "scheduled_today_detail")
limit_raw = os.environ.get("REDSOIL_DETAIL_LIMIT", "15").strip()
limit = int(limit_raw) if limit_raw else 15

init_db()
load_runtime_config()

kwargs = {
    "target_date": target_date,
    "trigger_type": trigger_type,
}
if limit > 0:
    kwargs["limit"] = limit

summary = submit_document_trigger_config("redsoil_detail", **kwargs)
print(summary)
'@

Set-Location $Root

for ($attempt = 1; $attempt -le $MaxRetries; $attempt++) {
    $startedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "[$startedAt] attempt=$attempt target_mode=$TargetMode limit=$Limit trigger_type=$TriggerType" | Tee-Object -FilePath $LogPath -Append

    $PythonCode | & $Python - 2>&1 | Tee-Object -FilePath $LogPath -Append
    $exitCode = $LASTEXITCODE
    if ($exitCode -eq 0) {
        $finishedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        "[$finishedAt] success" | Tee-Object -FilePath $LogPath -Append
        exit 0
    }

    $failedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "[$failedAt] failed exit_code=$exitCode" | Tee-Object -FilePath $LogPath -Append
    if ($attempt -lt $MaxRetries) {
        Start-Sleep -Seconds $RetryDelaySeconds
    }
}

exit 1
