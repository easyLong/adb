param(
    [string]$App = "alipay_crawler",

    [ValidateSet("scheduler", "supervisor", "db", "fetch", "check", "batch", "report")]
    [string]$Task = "scheduler",

    [string]$Python = "python",
    [string]$TencentEnvFile = "D:\password\tengxun.txt",
    [string]$MysqlEnvFile = "D:\password\mysql.txt"
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

if (-not $env:TENCENT_DOC_URL) {
    $env:TENCENT_DOC_URL = "https://docs.qq.com/sheet/DY1hCSG96TkVySmp1?tab=BB08J2"
}
if (-not $env:TENCENT_DOC_FILE_ID) {
    $env:TENCENT_DOC_FILE_ID = "DY1hCSG96TkVySmp1"
}
if (-not $env:TENCENT_DOC_SHEET_ID) {
    $env:TENCENT_DOC_SHEET_ID = "BB08J2"
}
if (-not $env:ADB_PATH) {
    $env:ADB_PATH = Join-Path $Root "platform-tools\adb.exe"
}

Set-Location $Root
$Module = "apps.$App.app"

if ($Task -eq "scheduler") {
    & $Python -m $Module
} elseif ($Task -eq "supervisor") {
    & $Python -m $Module --supervise
} else {
    & $Python -m $Module --once $Task
}
