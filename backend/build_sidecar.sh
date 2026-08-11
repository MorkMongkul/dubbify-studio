#!/usr/bin/env bash
# Builds the standalone FastAPI sidecar binary for the Tauri desktop app and
# drops it into frontend/src-tauri/binaries/ with the target-triple suffix
# Tauri's `externalBin` mechanism expects.
#
# Requires: `python3 -m venv venv_clean && venv_clean/bin/pip install -r
# requirements.txt pyinstaller` (or reuse an existing venv with those installed).
#
# Usage: ./build_sidecar.sh [target-triple]
#   e.g. ./build_sidecar.sh aarch64-apple-darwin   (Apple Silicon, default)
#        ./build_sidecar.sh x86_64-apple-darwin    (Intel Mac)
set -euo pipefail
cd "$(dirname "$0")"

TARGET_TRIPLE="${1:-aarch64-apple-darwin}"
VENV="${VENV:-venv_clean}"
OUT_DIR="../frontend/src-tauri/binaries"
BIN_NAME="dubify-backend-${TARGET_TRIPLE}"

rm -rf build dist dubify-backend.spec

# NOTE: bundles backend/.env (DATABASE_URL, GEMINI_API_KEY, HF_TOKEN, ...)
# directly into the binary so the sidecar can find its config regardless of
# the cwd Tauri launches it with (see app/core/config.py's _env_file_path).
# Fine for a binary that stays on your own machine; do NOT hand this
# executable to anyone else without swapping to a proper secrets story first.
"$VENV/bin/pyinstaller" --onefile --name dubify-backend \
  --add-data "app/assets:app/assets" \
  --add-data "../frontend/dist:frontend_dist" \
  --add-data ".env:." \
  --hidden-import uvicorn.protocols.http.httptools_impl \
  --hidden-import uvicorn.protocols.websockets.websockets_impl \
  --hidden-import uvicorn.lifespan.on \
  --hidden-import uvicorn.loops.uvloop \
  --hidden-import sqlalchemy.dialects.postgresql.asyncpg \
  --hidden-import sqlalchemy.dialects.postgresql.psycopg2 \
  --hidden-import sqlalchemy.dialects.sqlite.aiosqlite \
  --hidden-import aiosqlite \
  sidecar_entry.py

mkdir -p "$OUT_DIR"
cp dist/dubify-backend "$OUT_DIR/$BIN_NAME"
rm -rf build dist dubify-backend.spec

echo "Sidecar binary written to $OUT_DIR/$BIN_NAME"
