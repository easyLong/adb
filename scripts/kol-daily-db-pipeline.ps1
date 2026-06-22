param(
    [string]$ReportDate = "",
    [switch]$StartWeb,
    [int]$WebPort = 8091,
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

if ($StartWeb) {
    $WebArgs = @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        $RunScript,
        "-Task",
        "kol-metrics-web",
        "-WebPort",
        "$WebPort"
    )

    if ($DryRun) {
        Write-Host (Format-CommandLine "Start-Process powershell" $WebArgs)
    } else {
        Start-Process -FilePath "powershell" `
            -ArgumentList $WebArgs `
            -WorkingDirectory $Root `
            -WindowStyle Hidden
        Write-Host "KOL metrics web: http://127.0.0.1:$WebPort/"
    }
}
