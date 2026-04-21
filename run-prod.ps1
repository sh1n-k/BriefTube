[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$RootDir = $PSScriptRoot
Set-Location -Path $RootDir

if (-not $env:APP_CONFIG_FILE) {
    $env:APP_CONFIG_FILE = "config.prod.yaml"
}
if (-not $env:HOST) {
    $env:HOST = "0.0.0.0"
}
if (-not $env:PORT) {
    $env:PORT = "8000"
}

$UvCommand = Get-Command uv -ErrorAction SilentlyContinue
if ($null -ne $UvCommand) {
    & $UvCommand.Source run python -m uvicorn app.main:app --host $env:HOST --port $env:PORT
    exit $LASTEXITCODE
}

$VenvPython = Join-Path $RootDir ".venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    & $VenvPython -m uvicorn app.main:app --host $env:HOST --port $env:PORT
    exit $LASTEXITCODE
}

$PythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($null -ne $PythonCommand) {
    & $PythonCommand.Source -m uvicorn app.main:app --host $env:HOST --port $env:PORT
    exit $LASTEXITCODE
}

$PyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($null -ne $PyLauncher) {
    & $PyLauncher.Source -m uvicorn app.main:app --host $env:HOST --port $env:PORT
    exit $LASTEXITCODE
}

throw "Python executable was not found. Create .venv or install Python 3.11+."
exit $LASTEXITCODE
