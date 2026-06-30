param(
    [string]$ReportDate = "",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$RunScript = Join-Path $PSScriptRoot "run.ps1"

function Format-CommandLine {
    param([string]$Command, [string[]]$Arguments)
    return "$Command $($Arguments -join ' ')"
}

$PipelineArgs = @("-Task", "kol-daily-db-pipeline")
if ($ReportDate) {
    $PipelineArgs += @("-ReportDate", $ReportDate)
}

if ($DryRun) {
    Write-Host (Format-CommandLine ".\scripts\run.ps1" $PipelineArgs)
} else {
    & $RunScript @PipelineArgs
    if ($LASTEXITCODE) {
        exit $LASTEXITCODE
    }
}
