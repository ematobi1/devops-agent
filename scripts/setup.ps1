$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
python -m venv .venv
& ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
& ".venv\Scripts\python.exe" -m pip install --quiet -e ./contracts -e ./services/orchestrator -e ./services/approval -e ./services/verifier -e ./services/codegen pytest
git config core.hooksPath scripts/hooks
Write-Host "done. run: .venv\Scripts\python.exe -m pytest"
