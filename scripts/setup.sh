#!/bin/sh
set -e
cd "$(dirname "$0")/.."
python -m venv .venv
".venv/Scripts/python" -m pip install --quiet --upgrade pip
".venv/Scripts/python" -m pip install --quiet -e ./contracts -e ./services/orchestrator -e ./services/approval -e ./services/verifier pytest
git config core.hooksPath scripts/hooks
echo "done. run: .venv/Scripts/python -m pytest"
