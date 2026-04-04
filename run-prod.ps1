[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$RootDir = $PSScriptRoot
Set-Location -Path $RootDir

$UvCommand = Get-Command uv -ErrorAction SilentlyContinue
if ($null -eq $UvCommand) {
    throw "uv is required. Install uv and run 'uv sync' first."
}

if (-not $env:APP_CONFIG_FILE) {
    $env:APP_CONFIG_FILE = "config.prod.yaml"
}
if (-not $env:HOST) {
    $env:HOST = "0.0.0.0"
}
if (-not $env:PORT) {
    $env:PORT = "8000"
}

& $UvCommand.Source run python -m uvicorn app.main:app --host $env:HOST --port $env:PORT
exit $LASTEXITCODE
