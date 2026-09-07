$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$meetingPython = Join-Path (Get-Location) '.venv\Scripts\python.exe'
if (-not (Test-Path $meetingPython)) {
    throw 'Run uv sync --frozen --extra asr first.'
}
& $meetingPython -m streamlit run app.py @args
exit $LASTEXITCODE
