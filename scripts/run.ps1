param(
    [string]$App = "finance_crawler",

    [ValidateSet("scheduler", "supervisor", "db", "config", "fetch", "check", "detail", "excel-detail", "link-detail", "report")]
    [string]$Task = "scheduler",

    [string]$Python = "python",
    [string]$EnvFile = ".env",
    [string]$TencentDocUrl = "",
    [string]$ExcelInputPath = "",
    [string]$SingleLink = "",
    [string]$ReportDate = "",
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
    & $Python -m $Module --once $Task
}

exit $LASTEXITCODE
