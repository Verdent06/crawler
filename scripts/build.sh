#!/usr/bin/env bash
# One build: Python deps + production React bundle.
set -euo pipefail
cd "$(dirname "$0")/.."
python -m pip install -r requirements.txt
npm ci --prefix frontend
npm run build --prefix frontend
