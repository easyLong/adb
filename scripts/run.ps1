param(
    [string]$App = "finance_crawler",

    [ValidateSet("scheduler", "supervisor", "db", "crawler-app-db", "config", "fetch", "check", "detail", "excel-detail", "link-detail", "report", "profile-sync", "profile-daily-rows", "profile-create-tasks", "profile-crawl", "profile-writeback", "profile-metrics", "profile-post-reads", "kol-daily-snapshot", "kol-daily-writeback", "kol-daily-crawl", "profile-trigger-list", "profile-trigger-run", "article-sync", "article-crawl", "article-writeback", "article-details", "doc-link-reads", "doc-columns-check", "v2-read-count-submit", "v2-read-count-crawl", "v2-read-count-writeback", "v2-read-count", "v2-initial-check-submit", "v2-initial-check-crawl", "v2-initial-check-writeback", "v2-initial-check", "v2-detail-submit", "v2-detail-crawl", "v2-detail-writeback", "v2-detail", "v2-doc-config-set", "v2-doc-config-check", "v2-doc-config-list", "v2-doc-config-submit", "v2-doc-config-run", "v2-trigger-set", "v2-trigger-bind", "v2-trigger-list", "v2-trigger-submit", "v2-submit-worker-once", "v2-crawl-worker-once", "v2-writeback-worker-once", "v2-correction-plan", "v2-correction-writeback", "v2-correction-apply")]
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
