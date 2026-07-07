param(
    [string]$PackageName = "com.ss.android.lark",
    [string]$DeviceSerial = "APH0219701010623",
    [string]$AdbPath = "",
    [string]$CalendarPath = "",
    [string]$LogPath = ""
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
if (-not $CalendarPath) {
    $CalendarPath = Join-Path $Root "config\china-workdays.json"
}
if (-not $LogPath) {
    $LogPath = Join-Path $Root "apps\finance_crawler\logs\open-lark-cn-workday.log"
}
if (-not $AdbPath) {
    $DefaultAdbPath = "D:\Tools\platform-tools\adb.exe"
    if (Test-Path -LiteralPath $DefaultAdbPath) {
        $AdbPath = $DefaultAdbPath
    } else {
        $AdbPath = "adb"
    }
}

function Write-RunLog {
    param([string]$Message)
    $dir = Split-Path -Parent $LogPath
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value "[$timestamp] $Message"
}

function Test-ChinaWorkday {
    param([datetime]$Date)
    if (-not (Test-Path -LiteralPath $CalendarPath)) {
        throw "China workday calendar not found: $CalendarPath"
    }
    $calendar = Get-Content -LiteralPath $CalendarPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $yearKey = $Date.ToString("yyyy")
    $dateKey = $Date.ToString("yyyy-MM-dd")
    $yearConfig = $calendar.$yearKey
    if (-not $yearConfig) {
        throw "China workday calendar missing year: $yearKey"
    }
    if ($yearConfig.holidays -contains $dateKey) {
        return $false
    }
    if ($yearConfig.workdays -contains $dateKey) {
        return $true
    }
    return $Date.DayOfWeek -in @([DayOfWeek]::Monday, [DayOfWeek]::Tuesday, [DayOfWeek]::Wednesday, [DayOfWeek]::Thursday, [DayOfWeek]::Friday)
}

$today = Get-Date
if (-not (Test-ChinaWorkday -Date $today)) {
    Write-RunLog "skip: non-China-workday date=$($today.ToString('yyyy-MM-dd'))"
    exit 0
}

& $AdbPath start-server | Out-Null
$devices = & $AdbPath devices
if (-not ($devices -match ("(?m)^" + [regex]::Escape($DeviceSerial) + "\s+device\b"))) {
    Write-RunLog "skip: target adb device not online serial=$DeviceSerial"
    exit 2
}

& $AdbPath -s $DeviceSerial shell input keyevent KEYCODE_WAKEUP | Out-Null
& $AdbPath -s $DeviceSerial shell monkey -p $PackageName -c android.intent.category.LAUNCHER 1 | Out-Null
Write-RunLog "opened package=$PackageName serial=$DeviceSerial date=$($today.ToString('yyyy-MM-dd'))"
