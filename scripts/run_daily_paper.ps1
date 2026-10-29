param(
    [string]$AsOf = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if ($AsOf) {
    uv run quant-daily-update run --as-of $AsOf
    uv run quant-paper run --as-of $AsOf
} else {
    uv run quant-daily-update run
    uv run quant-paper run-latest
}

uv run quant-paper status
