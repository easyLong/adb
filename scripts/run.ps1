param(
    [string]$App = "finance_crawler",

    [ValidateSet("scheduler", "supervisor", "db", "config", "fetch", "check", "detail", "excel-detail", "link-detail", "report")]
    [string]$Task = "scheduler",

    [string]$Python = "python",
    [string]$TencentEnvFile = "D:\password\tengxun.txt",
    [string]$MysqlEnvFile = "D:\password\mysql.txt",
    [string]$TencentDocUrl = "",
    [string]$ExcelInputPath = "",
    [string]$SingleLink = "",
    [string[]]$ConfigSet = @()
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")

function Load-TencentEnv {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    $content = Get-Content -LiteralPath $Path -Raw
    foreach ($line in ($content -split "`n")) {
        if ($line -match '\$env:([A-Z0-9_]+)\s*=\s*"([^"]+)"') {
            [Environment]::SetEnvironmentVariable($matches[1], $matches[2], "Process")
        }
    }
}

function Load-MysqlEnv {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    $content = Get-Content -LiteralPath $Path -Raw
    $env:MYSQL_HOST = [regex]::Match($content, "'host':\s*'([^']+)'").Groups[1].Value
    $env:MYSQL_PORT = [regex]::Match($content, "'port':\s*(\d+)").Groups[1].Value
    $env:MYSQL_USER = [regex]::Match($content, "'user':\s*'([^']+)'").Groups[1].Value
    $env:MYSQL_PASSWORD = [regex]::Match($content, "'password':\s*'([^']+)'").Groups[1].Value
    $env:MYSQL_DATABASE = [regex]::Match($content, "'database':\s*'([^']+)'").Groups[1].Value
}

Load-TencentEnv $TencentEnvFile
Load-MysqlEnv $MysqlEnvFile

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
} else {
    & $Python -m $Module --once $Task
}

exit $LASTEXITCODE
