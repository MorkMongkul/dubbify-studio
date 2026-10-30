#!/usr/bin/env bash
# Builds the FastAPI backend that the Tauri desktop app bundles, as a folder:
# frontend/src-tauri/backend/ (the movelo-backend executable plus its
# libraries in _internal/). The release script ships it in the app's
# Resources.
#
# A folder, not a single file: a one-file build unpacks itself into a new
# temporary folder on every launch, and macOS re-checks every library it
# unpacks, which took 14 s on every launch (35 s after a restart). Unpacked
# once at install, the backend is ready in under a second.
#
# Needs a build venv whose Python has the SAME architecture as the target
# (PyInstaller can't cross-compile):
#   python3 -m venv venv_clean
#   venv_clean/bin/pip install -r requirements.txt -r requirements-build.txt
# For Intel on an Apple Silicon Mac, create the venv from an x86_64 Python
# under Rosetta, e.g. `arch -x86_64 /usr/local/bin/python3 -m venv venv_clean_x86_64`.
#
# Burned-in Khmer subtitles need FriBiDi (scripts/build-fribidi.sh <target>);
# without vendor/fribidi/<target> they'd be drawn unshaped, so the build stops.
# ALLOW_UNSHAPED_KHMER=1 builds anyway (development only).
#
# Usage:
#   ./build_sidecar.sh                                              # Apple Silicon
#   VENV=venv_clean_x86_64 ./build_sidecar.sh x86_64-apple-darwin   # Intel
# SKIP_FRONTEND_BUILD=1 skips rebuilding frontend/dist (the caller just did).
#
# No secrets go into the build: API keys are entered in the app's Settings
# and stored per user in ~/Library/Application Support/com.movelo.studio.
set -euo pipefail
cd "$(dirname "$0")"

TARGET_TRIPLE="${1:-aarch64-apple-darwin}"
VENV="${VENV:-venv_clean}"
OUT_DIR="../frontend/src-tauri/backend"
FRIBIDI="$(cd .. && pwd)/vendor/fribidi/$TARGET_TRIPLE/libfribidi.dylib"

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

EXTRA=()
if [ -f "$FRIBIDI" ]; then
  EXTRA+=(--add-binary "$FRIBIDI:.")
elif [ "${ALLOW_UNSHAPED_KHMER:-0}" = "1" ]; then
  echo "warning: no $FRIBIDI — burned-in Khmer subtitles will be unshaped in this build." >&2
else
  echo "error: no $FRIBIDI — build it with: scripts/build-fribidi.sh $TARGET_TRIPLE" >&2
  echo "       (ALLOW_UNSHAPED_KHMER=1 builds without it, for development only)" >&2
  exit 1
fi

# The backend serves frontend/dist — build it now so a stale UI never ships.
if [ "${SKIP_FRONTEND_BUILD:-0}" != "1" ]; then
  (cd ../frontend && npm run build)
fi

WORK="$(mktemp -d "${TMPDIR:-/tmp}/movelo-backend-build.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
"$VENV/bin/pyinstaller" --onedir --noconfirm --name movelo-backend \
  --distpath "$WORK/dist" --workpath "$WORK/build" --specpath "$WORK" \
  --add-data "$PWD/app/assets:app/assets" \
  --add-data "$PWD/../frontend/dist:frontend_dist" \
  ${EXTRA[@]+"${EXTRA[@]}"} \
  --hidden-import uvicorn.protocols.http.httptools_impl \
  --hidden-import uvicorn.protocols.websockets.websockets_impl \
  --hidden-import uvicorn.lifespan.on \
  --hidden-import uvicorn.loops.uvloop \
  --hidden-import sqlalchemy.dialects.postgresql.asyncpg \
  --hidden-import sqlalchemy.dialects.postgresql.psycopg2 \
  --hidden-import sqlalchemy.dialects.sqlite.aiosqlite \
  --hidden-import aiosqlite \
  --collect-data opencc \
  "$PWD/sidecar_entry.py"

# The app ships only an LGPL ffmpeg: stop if a library pulled in a GPL or non-free codec
# (OpenCV's macOS wheel bundles a whole FFmpeg with x264, x265 and others).
BAD="$(find "$WORK/dist/movelo-backend" \( -iname '*libx264*' -o -iname '*libx265*' -o -iname '*libpostproc*' \
  -o -iname '*libvidstab*' -o -iname '*librubberband*' -o -iname '*libxvidcore*' -o -iname '*libfdk-aac*' \) | head -5)"
if [ -n "$BAD" ]; then
  echo "error: the build contains GPL or non-free libraries:" >&2
  echo "$BAD" >&2
  exit 1
fi

rm -rf "$OUT_DIR"
mkdir -p "$(dirname "$OUT_DIR")"
mv "$WORK/dist/movelo-backend" "$OUT_DIR"

# Check what the app will actually get, from a neutral folder and environment
# (dlopen also searches the current folder, which would hide a missing library).
(cd / && env -i HOME="$WORK" PATH=/usr/bin:/bin "$OLDPWD/$OUT_DIR/movelo-backend" --self-test) \
  || [ "${ALLOW_UNSHAPED_KHMER:-0}" = "1" ]

echo "Backend written to $OUT_DIR ($(lipo -archs "$OUT_DIR/movelo-backend" 2>/dev/null || echo "$WANT_ARCH"), $(du -sh "$OUT_DIR" | cut -f1))"
