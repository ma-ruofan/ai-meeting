$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
if (Test-Path '.venv\Scripts\python.exe') {
    & '.venv\Scripts\python.exe' 'scripts\start.py' @args
} else {
    python 'scripts\start.py' @args
}
exit $LASTEXITCODE
