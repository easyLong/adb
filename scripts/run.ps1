param(
    [string]$App = "finance_crawler",

    [ValidateSet("scheduler", "supervisor", "workers-start", "workers-status", "workers-stop", "db", "crawler-app-db", "device-pool-status", "device-pool-refresh", "config", "fetch", "check", "detail", "excel-detail", "link-detail", "report", "profile-sync", "profile-daily-rows", "profile-create-tasks", "profile-crawl", "profile-writeback", "profile-metrics", "profile-post-reads", "kol-daily-snapshot", "kol-daily-writeback", "kol-daily-crawl", "kol-tenpay-external-reads", "profile-trigger-list", "profile-trigger-run", "article-sync", "article-crawl", "article-writeback", "article-details", "doc-link-reads", "doc-columns-check", "v2-read-count-submit", "v2-read-count-crawl", "v2-read-count-writeback", "v2-read-count", "v2-initial-check-submit", "v2-initial-check-crawl", "v2-initial-check-writeback", "v2-initial-check", "v2-detail-submit", "v2-detail-crawl", "v2-detail-writeback", "v2-detail", "v2-doc-config-set", "v2-doc-config-check", "v2-doc-config-list", "v2-doc-config-submit", "v2-doc-config-run", "v2-trigger-set", "v2-trigger-bind", "v2-trigger-list", "v2-trigger-submit", "v2-submit-worker-once", "v2-crawl-worker-once", "v2-writeback-worker-once", "v2-correction-plan", "v2-correction-writeback", "v2-correction-apply")]
    [string]$Task = "scheduler",

    [string]$Python = "python",
    [string]$EnvFile = ".env",
    [string]$TencentDocUrl = "",
    [string]$ExcelInputPath = "",
    [string]$SingleLink = "",
    [string]$ReportDate = "",
    [string]$DocumentConfigKey = "",
    [string]$DocumentTaskType = "",
    [string]$DocumentFields = "",
    [string]$DocumentDescription = "",
    [string]$DocumentSheetMode = "",
    [string]$DocumentSheetId = "",
    [string]$DocumentSheetTitle = "",
    [string]$DocumentSheetKeyword = "",
    [string]$DocumentSheetIds = "",
    [int]$SubmitScanIntervalSeconds = 300,
    [int]$SubmitTargetDateOffsetDays = 0,
    [int]$CorrectionDocumentId = 0,
    [string]$CorrectionSheetId = "",
    [int]$CorrectionRowIndex = 0,
    [string]$CorrectionPostUrl = "",
    [string]$CorrectionField = "",
    [string]$CorrectionValue = "",
    [string]$CorrectionReason = "",
    [string]$CorrectionOperator = "cli",
    [string]$SchedulerRoles = "",
    [string]$TencentDocScanMode = "",
    [string]$TencentDocScanDate = "",
    [string]$TencentDocSheetTitleFilter = "",
    [string]$DetailSourceDates = "",
    [string[]]$ConfigSet = @()
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")

function Load-ProjectEnv {
    param([string]$Path)
    $ResolvedPath = if ([System.IO.Path]::IsPathRooted($Path)) { $Path } else { Join-Path $Root $Path }
    if (-not (Test-Path -LiteralPath $ResolvedPath)) {
        return
    }

    foreach ($line in Get-Content -LiteralPath $ResolvedPath) {
        if ($line -match '^\s*(?:\$env:)?(\uFEFF?MYSQL_[A-Z0-9_]+)\s*=\s*(.*)\s*$') {
            $name = $matches[1].TrimStart([char]0xFEFF)
            $value = $matches[2].Trim().Trim('"').Trim("'")
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

Load-ProjectEnv $EnvFile

function Get-QueueWorkerDefinitions {
    @(
        [pscustomobject]@{ Name = "submit-heartbeat"; Roles = "submit,heartbeat" },
        [pscustomobject]@{ Name = "crawl"; Roles = "crawl" },
        [pscustomobject]@{ Name = "writeback"; Roles = "writeback" },
        [pscustomobject]@{ Name = "profile"; Roles = "profile" }
    )
}

function Get-WorkerRuntimeDir {
    $Path = Join-Path $Root "apps\finance_crawler\logs\queue_workers"
    New-Item -ItemType Directory -Force -Path $Path | Out-Null
    return $Path
}

function Get-WorkerPidPath {
    param([string]$Name)
    Join-Path (Get-WorkerRuntimeDir) "$Name.pid"
}

function Get-WorkerProcessInfo {
    param([string]$Name)
    $PidPath = Get-WorkerPidPath $Name
    if (-not (Test-Path -LiteralPath $PidPath)) {
        return $null
    }
    $PidLine = Get-Content -LiteralPath $PidPath -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $PidLine) {
        return $null
    }
    $WorkerPid = $PidLine.Trim()
    if (-not $WorkerPid -or $WorkerPid -notmatch '^\d+$') {
        return $null
    }
    try {
        return Get-Process -Id ([int]$WorkerPid) -ErrorAction Stop
    } catch {
        return $null
    }
}

function Start-QueueWorkers {
    $RuntimeDir = Get-WorkerRuntimeDir
    foreach ($Worker in Get-QueueWorkerDefinitions) {
        $Existing = Get-WorkerProcessInfo $Worker.Name
        if ($Existing) {
            Write-Host "already running: $($Worker.Name) pid=$($Existing.Id) roles=$($Worker.Roles)"
            continue
        }

        $OutFile = Join-Path $RuntimeDir "$($Worker.Name).out.log"
        $ErrFile = Join-Path $RuntimeDir "$($Worker.Name).err.log"
        $Args = @("-m", $Module)
        $PreviousSchedulerRoles = $env:SCHEDULER_ROLES
        $env:SCHEDULER_ROLES = $Worker.Roles
        try {
            $Process = Start-Process -FilePath $Python `
                -ArgumentList $Args `
                -WorkingDirectory $Root `
                -WindowStyle Hidden `
                -RedirectStandardOutput $OutFile `
                -RedirectStandardError $ErrFile `
                -PassThru
        } finally {
            $env:SCHEDULER_ROLES = $PreviousSchedulerRoles
        }
        Set-Content -LiteralPath (Get-WorkerPidPath $Worker.Name) -Value $Process.Id
        Write-Host "started: $($Worker.Name) pid=$($Process.Id) roles=$($Worker.Roles)"
    }
    Write-Host "logs: $RuntimeDir"
}

function Show-QueueWorkerStatus {
    $RuntimeDir = Get-WorkerRuntimeDir
    foreach ($Worker in Get-QueueWorkerDefinitions) {
        $Process = Get-WorkerProcessInfo $Worker.Name
        if ($Process) {
            Write-Host "running: $($Worker.Name) pid=$($Process.Id) roles=$($Worker.Roles)"
        } else {
            Write-Host "stopped: $($Worker.Name) roles=$($Worker.Roles)"
        }
    }
    Write-Host "logs: $RuntimeDir"
}

function Stop-QueueWorkers {
    foreach ($Worker in Get-QueueWorkerDefinitions) {
        $PidPath = Get-WorkerPidPath $Worker.Name
        $Process = Get-WorkerProcessInfo $Worker.Name
        if ($Process) {
            Stop-Process -Id $Process.Id -Force
            Write-Host "stopped: $($Worker.Name) pid=$($Process.Id)"
        } else {
            Write-Host "not running: $($Worker.Name)"
        }
        if (Test-Path -LiteralPath $PidPath) {
            Remove-Item -LiteralPath $PidPath -Force
        }
    }
}

if ($TencentDocScanDate -and -not $TencentDocScanMode) {
    $TencentDocScanMode = "date"
}
if ($TencentDocScanMode) {
    $AllowedScanModes = @("single", "today", "date", "filter", "all")
    if ($AllowedScanModes -notcontains $TencentDocScanMode) {
        throw "Invalid TencentDocScanMode: $TencentDocScanMode. Allowed: $($AllowedScanModes -join ', ')"
    }
    $env:TENCENT_DOC_SCAN_MODE = $TencentDocScanMode
}
if ($TencentDocScanDate) {
    if ($TencentDocScanDate -notmatch '^\d{4}-\d{2}-\d{2}$') {
        throw "Invalid TencentDocScanDate: $TencentDocScanDate. Expected yyyy-MM-dd."
    }
    $env:TENCENT_DOC_SCAN_DATE = $TencentDocScanDate
}
if ($TencentDocSheetTitleFilter) {
    $env:TENCENT_DOC_SHEET_TITLE_FILTER = $TencentDocSheetTitleFilter
}
if ($DetailSourceDates) {
    $DetailSourceDates -split "," | ForEach-Object {
        $date = $_.Trim()
        if ($date -and $date -notmatch '^\d{4}-\d{2}-\d{2}$') {
            throw "Invalid DetailSourceDates item: $date. Expected comma-separated yyyy-MM-dd."
        }
    }
    $env:DETAIL_SOURCE_DATES = $DetailSourceDates
}
if ($SchedulerRoles) {
    $env:SCHEDULER_ROLES = $SchedulerRoles
}

if (-not $env:ADB_PATH) {
    $BundledAdb = Join-Path $Root "platform-tools\adb.exe"
    if (Test-Path -LiteralPath $BundledAdb) {
        $env:ADB_PATH = $BundledAdb
    } else {
        $env:ADB_PATH = "adb"
    }
}

Set-Location $Root
$Module = "apps.$App.app"

if ($Task -eq "scheduler") {
    & $Python -m $Module
} elseif ($Task -eq "supervisor") {
    & $Python -m $Module --supervise
} elseif ($Task -eq "workers-start") {
    Start-QueueWorkers
} elseif ($Task -eq "workers-status") {
    Show-QueueWorkerStatus
} elseif ($Task -eq "workers-stop") {
    Stop-QueueWorkers
} elseif ($Task -eq "config") {
    $ConfigArgs = @("--once", "config")
    if ($TencentDocUrl) {
        $ConfigArgs += @("--tencent-doc-url", $TencentDocUrl)
    }
    if ($ExcelInputPath) {
        $ConfigArgs += @("--excel-input-path", $ExcelInputPath)
    }
    if ($SingleLink) {
        $ConfigArgs += @("--single-link", $SingleLink)
    }
    foreach ($Item in $ConfigSet) {
        $ConfigArgs += @("--config-set", $Item)
    }
    & $Python -m $Module @ConfigArgs
} elseif ($Task -eq "link-detail") {
    $LinkArgs = @("--once", "link-detail")
    if ($SingleLink) {
        $LinkArgs += @("--single-link", $SingleLink)
    }
    & $Python -m $Module @LinkArgs
} elseif ($Task -eq "report") {
    $ReportArgs = @("--once", "report")
    if ($ReportDate) {
        $ReportArgs += @("--report-date", $ReportDate)
    }
    & $Python -m $Module @ReportArgs
} else {
    $OnceArgs = @("--once", $Task)
    if ($TencentDocUrl) {
        $OnceArgs += @("--tencent-doc-url", $TencentDocUrl)
    }
    if ($ReportDate) {
        $OnceArgs += @("--report-date", $ReportDate)
    }
    if ($DocumentConfigKey) {
        $OnceArgs += @("--document-config-key", $DocumentConfigKey)
    }
    if ($DocumentTaskType) {
        $OnceArgs += @("--document-task-type", $DocumentTaskType)
    }
    if ($DocumentFields) {
        $OnceArgs += @("--document-fields", $DocumentFields)
    }
    if ($DocumentDescription) {
        $OnceArgs += @("--document-description", $DocumentDescription)
    }
    if ($DocumentSheetMode) {
        $OnceArgs += @("--document-sheet-mode", $DocumentSheetMode)
    }
    if ($DocumentSheetId) {
        $OnceArgs += @("--document-sheet-id", $DocumentSheetId)
    }
    if ($DocumentSheetTitle) {
        $OnceArgs += @("--document-sheet-title", $DocumentSheetTitle)
    }
    if ($DocumentSheetKeyword) {
        $OnceArgs += @("--document-sheet-keyword", $DocumentSheetKeyword)
    }
    if ($DocumentSheetIds) {
        $OnceArgs += @("--document-sheet-ids", $DocumentSheetIds)
    }
    if ($SubmitScanIntervalSeconds) {
        $OnceArgs += @("--submit-scan-interval-seconds", $SubmitScanIntervalSeconds)
    }
    if ($SubmitTargetDateOffsetDays) {
        $OnceArgs += @("--submit-target-date-offset-days", $SubmitTargetDateOffsetDays)
    }
    if ($CorrectionDocumentId) {
        $OnceArgs += @("--correction-document-id", $CorrectionDocumentId)
    }
    if ($CorrectionSheetId) {
        $OnceArgs += @("--correction-sheet-id", $CorrectionSheetId)
    }
    if ($CorrectionRowIndex) {
        $OnceArgs += @("--correction-row-index", $CorrectionRowIndex)
    }
    if ($CorrectionPostUrl) {
        $OnceArgs += @("--correction-post-url", $CorrectionPostUrl)
    }
    if ($CorrectionField) {
        $OnceArgs += @("--correction-field", $CorrectionField)
    }
    if ($CorrectionValue) {
        $OnceArgs += @("--correction-value", $CorrectionValue)
    }
    if ($CorrectionReason) {
        $OnceArgs += @("--correction-reason", $CorrectionReason)
    }
    if ($CorrectionOperator) {
        $OnceArgs += @("--correction-operator", $CorrectionOperator)
    }
    foreach ($Item in $ConfigSet) {
        $OnceArgs += @("--config-set", $Item)
    }
    & $Python -m $Module @OnceArgs
}

exit $LASTEXITCODE
