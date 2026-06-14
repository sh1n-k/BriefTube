[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$RootDir = $PSScriptRoot
Set-Location -Path $RootDir

if ([string]::IsNullOrWhiteSpace($env:BRIEFTUBE_REMOTE_SYNC_DSN)) {
    throw "BRIEFTUBE_REMOTE_SYNC_DSN is required for remote sync."
}

if (-not $env:APP_CONFIG_FILE) {
    $env:APP_CONFIG_FILE = "config.dev.yaml"
}
if (-not $env:UV_LINK_MODE) {
    $env:UV_LINK_MODE = "copy"
}
if (-not $env:BRIEFTUBE_REMOTE_SYNC_ENABLED) {
    $env:BRIEFTUBE_REMOTE_SYNC_ENABLED = "true"
}

$UvCommand = Get-Command uv -ErrorAction SilentlyContinue
if ($null -ne $UvCommand) {
    & $UvCommand.Source run brieftube
    exit $LASTEXITCODE
}

$VenvPython = Join-Path $RootDir ".venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    & $VenvPython -m app.cli
    exit $LASTEXITCODE
}

$PythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($null -ne $PythonCommand) {
    & $PythonCommand.Source -m app.cli
    exit $LASTEXITCODE
}

$PyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($null -ne $PyLauncher) {
    & $PyLauncher.Source -m app.cli
    exit $LASTEXITCODE
}

throw "Python executable was not found. Create .venv or install Python 3.11+."
exit $LASTEXITCODE
