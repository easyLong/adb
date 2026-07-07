param(
    [ValidateSet("run", "install", "status", "start", "uninstall")]
    [string]$Action = "run",
    [string]$TaskName = "ADB KOL Settlement Metrics Daily 2300",
    [string]$Time = "23:00",
    [string]$EnvFile = ".env",
    [ValidateSet("all", "today", "yesterday")]
    [string]$TargetMode = "all",
    [string]$ReportDate = "",
    [int]$Limit = 0,
    [int]$MaxRetries = 1,
    [int]$RetryDelaySeconds = 30
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$RunScript = Join-Path $Root "scripts\run.ps1"
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
        if ($taskAction.WorkingDirectory) {
            Write-Host "  working_directory: $($taskAction.WorkingDirectory)"
        }
    }
    Write-Host "triggers:"
    foreach ($trigger in $task.Triggers) {
        Write-Host "  $($trigger.StartBoundary) enabled=$($trigger.Enabled)"
    }
}

function Resolve-ReportDate {
    if ($ReportDate) {
        if ($ReportDate -notmatch '^\d{4}-\d{2}-\d{2}$') {
            throw "Invalid ReportDate: $ReportDate. Expected yyyy-MM-dd."
        }
        return $ReportDate
    }
    if ($TargetMode -eq "today") {
        return (Get-Date).ToString("yyyy-MM-dd")
    }
    if ($TargetMode -eq "yesterday") {
        return (Get-Date).AddDays(-1).ToString("yyyy-MM-dd")
    }
    return ""
}

if ($Action -eq "install") {
    $ScheduleScript = ".\scripts\kol-settlement-metrics-schedule.ps1"
    $ArgumentParts = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$ScheduleScript`"",
        "-Action", "run",
        "-EnvFile", "`"$EnvFile`"",
        "-TargetMode", $TargetMode,
        "-Limit", $Limit,
        "-MaxRetries", $MaxRetries,
        "-RetryDelaySeconds", $RetryDelaySeconds
    )
    if ($ReportDate) {
        $ArgumentParts += @("-ReportDate", "`"$ReportDate`"")
    }

    $TaskAction = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument ($ArgumentParts -join " ") `
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
        -Description "Run KOL settlement metrics submit/crawl/writeback." `
        -Force | Out-Null

    Write-Host "registered task: $TaskName"
    Write-Host "time: $Time"
    Write-Host "target_mode: $TargetMode"
    Write-Host "limit: $Limit"
    Write-Host "script: $ScheduleScript"
    Write-Host "working_directory: $Root"
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

if (-not (Test-Path -LiteralPath $RunScript)) {
    throw "run script not found: $RunScript"
}

$ResolvedReportDate = Resolve-ReportDate
$LogPath = Join-Path $LogDir ("kol_settlement_metrics_{0}.log" -f (Get-Date -Format "yyyyMMdd"))

$RunArgs = @("-Task", "kol-settlement-metrics", "-EnvFile", $EnvFile)
if ($ResolvedReportDate) {
    $RunArgs += @("-ReportDate", $ResolvedReportDate)
}
if ($Limit -gt 0) {
    $RunArgs += @("-Limit", $Limit)
}

Set-Location $Root

for ($attempt = 1; $attempt -le $MaxRetries; $attempt++) {
    $startedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "[$startedAt] attempt=$attempt target_mode=$TargetMode report_date=$ResolvedReportDate limit=$Limit" |
        Tee-Object -FilePath $LogPath -Append

    $PreviousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $RunScript @RunArgs 2>&1 |
            ForEach-Object { "$_" } |
            Tee-Object -FilePath $LogPath -Append
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }

    $finishedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "[$finishedAt] exit_code=$exitCode" | Tee-Object -FilePath $LogPath -Append

    if ($exitCode -eq 0) {
        exit 0
    }
    if ($attempt -lt $MaxRetries) {
        Start-Sleep -Seconds $RetryDelaySeconds
    }
}

exit $exitCode
