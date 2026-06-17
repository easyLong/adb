param(
    [Parameter(Mandatory = $true)]
    [string]$GroupName,

    [Parameter(Mandatory = $true)]
    [string]$Date,

    [int]$Pages = 12,
    [string]$OutDir = "exports\wechat",
    [string]$Serial = "",
    [switch]$SkipNavigation,
    [switch]$KeepOnDevice,
    [switch]$NoSearch
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$PythonScript = Join-Path $PSScriptRoot "wechat_chat_export.py"

$ArgsList = @(
    "--group-name", $GroupName,
    "--date", $Date,
    "--pages", "$Pages",
    "--out-dir", (Join-Path $Root $OutDir)
)

if ($Serial) {
    $ArgsList += @("--serial", $Serial)
}
if ($SkipNavigation) {
    $ArgsList += "--skip-navigation"
}
if ($KeepOnDevice) {
    $ArgsList += "--keep-on-device"
}
if ($NoSearch) {
    $ArgsList += "--no-search"
}

python $PythonScript @ArgsList
