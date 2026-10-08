#!/bin/sh
# One start: uvicorn on $PORT (Render/Railway) or 8000 locally.
set -eu
cd "$(dirname "$0")/.."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
