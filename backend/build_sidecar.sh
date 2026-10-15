#!/usr/bin/env bash
# Builds the FastAPI backend into the single-file "sidecar" binary the Tauri
# desktop app bundles, named with the target triple Tauri's externalBin expects.
#
# Needs a build venv whose Python has the SAME architecture as the target
# (PyInstaller can't cross-compile):
#   python3 -m venv venv_clean
#   venv_clean/bin/pip install -r requirements.txt -r requirements-build.txt
# For Intel on an Apple Silicon Mac, create the venv from an x86_64 Python
# under Rosetta, e.g. `arch -x86_64 /usr/local/bin/python3 -m venv venv_clean_x86_64`.
#
# Usage:
#   ./build_sidecar.sh                                              # Apple Silicon
#   VENV=venv_clean_x86_64 ./build_sidecar.sh x86_64-apple-darwin   # Intel
# SKIP_FRONTEND_BUILD=1 skips rebuilding frontend/dist (the caller just did).
#
# No secrets go into the binary: API keys are entered in the app's Settings
# and stored per user in ~/Library/Application Support/studio.dubify.desktop.
set -euo pipefail
cd "$(dirname "$0")"

TARGET_TRIPLE="${1:-aarch64-apple-darwin}"
VENV="${VENV:-venv_clean}"
OUT_DIR="../frontend/src-tauri/binaries"
BIN_NAME="dubify-backend-${TARGET_TRIPLE}"

case "$TARGET_TRIPLE" in
  aarch64-apple-darwin) WANT_ARCH=arm64 ;;
  x86_64-apple-darwin)  WANT_ARCH=x86_64 ;;
  *) echo "Unsupported target: $TARGET_TRIPLE" >&2; exit 1 ;;
esac
PY_ARCH="$("$VENV/bin/python" -c 'import platform; print(platform.machine())')"
if [ "$PY_ARCH" != "$WANT_ARCH" ]; then
  echo "error: $VENV runs $PY_ARCH Python, but $TARGET_TRIPLE needs $WANT_ARCH." >&2
  echo "       See the top of this script for creating an $WANT_ARCH build venv." >&2
  exit 1
fi

# The binary embeds frontend/dist — build it now so a stale UI never ships.
if [ "${SKIP_FRONTEND_BUILD:-0}" != "1" ]; then
  (cd ../frontend && npm run build)
fi

rm -rf build dist dubify-backend.spec
"$VENV/bin/pyinstaller" --onefile --name dubify-backend \
  --add-data "app/assets:app/assets" \
  --add-data "../frontend/dist:frontend_dist" \
  --hidden-import uvicorn.protocols.http.httptools_impl \
  --hidden-import uvicorn.protocols.websockets.websockets_impl \
  --hidden-import uvicorn.lifespan.on \
  --hidden-import uvicorn.loops.uvloop \
  --hidden-import sqlalchemy.dialects.postgresql.asyncpg \
  --hidden-import sqlalchemy.dialects.postgresql.psycopg2 \
  --hidden-import sqlalchemy.dialects.sqlite.aiosqlite \
  --hidden-import aiosqlite \
  --collect-data opencc \
  sidecar_entry.py

mkdir -p "$OUT_DIR"
cp dist/dubify-backend "$OUT_DIR/$BIN_NAME"
rm -rf build dist dubify-backend.spec

echo "Sidecar written to $OUT_DIR/$BIN_NAME ($(lipo -archs "$OUT_DIR/$BIN_NAME" 2>/dev/null || echo "$WANT_ARCH"))"
