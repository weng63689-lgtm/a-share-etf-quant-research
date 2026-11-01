param(
    [switch]$DryRun,
    [double]$InitialCapital = 500000,
    [ValidateSet("baseline", "stop08")]
    [string]$Variant = "baseline"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (-not $env:QUANTDINGER_BASE_URL) {
    $env:QUANTDINGER_BASE_URL = [Environment]::GetEnvironmentVariable(
        "QUANTDINGER_BASE_URL",
        "User"
    )
}
if (-not $env:QUANTDINGER_AGENT_TOKEN) {
    $env:QUANTDINGER_AGENT_TOKEN = [Environment]::GetEnvironmentVariable(
        "QUANTDINGER_AGENT_TOKEN",
        "User"
    )
}

if ($DryRun) {
    uv run quant-qd-deploy --dry-run --initial-capital $InitialCapital --variant $Variant
} else {
    uv run quant-qd-deploy --initial-capital $InitialCapital --variant $Variant
}
