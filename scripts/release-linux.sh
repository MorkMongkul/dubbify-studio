#!/usr/bin/env bash
# Builds the Linux desktop app (x86_64): a .deb and an AppImage.
#   scripts/release-linux.sh [extra `tauri build` flags]
#
# Steps: frontend → backend folder (serves the frontend; goes into the app's
# resources) → Tauri bundle under frontend/src-tauri/target/<target>/release/bundle/.
#
# Build it on the OLDEST Linux you want to support (CI uses ubuntu-22.04):
# the backend's libraries are linked against the build machine's glibc.
#
# Needs: the Tauri system packages (libwebkit2gtk-4.1-dev, libgtk-3-dev,
# librsvg2-dev, libayatana-appindicator3-dev, patchelf, ...), Rust, Node, and a
# backend build venv with Python 3.11:
#   python3.11 -m venv backend/venv_clean
#   backend/venv_clean/bin/pip install -r backend/requirements.txt -r backend/requirements-build.txt
# and, once, scripts/build-fribidi.sh x86_64-unknown-linux-gnu (needs gcc, make).
#
# ffmpeg is NOT bundled: the .deb depends on the system's `ffmpeg` package and the
# AppImage needs one installed (Settings says so when there is none). Bundling one
# would mean either no H.264 (the macOS build's LGPL-only ffmpeg) or a GPL x264.
#
# No in-app updates on Linux: the .deb updates with the package manager, and the
# AppImage by downloading the new one (src-tauri/src/updater.rs).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET=x86_64-unknown-linux-gnu
cd "$ROOT"

if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
  echo "error: this builds the Linux x86_64 app on a Linux x86_64 machine." >&2
  exit 1
fi
if [ ! -f "vendor/fribidi/$TARGET/libfribidi.so.0" ]; then
  scripts/build-fribidi.sh "$TARGET"
fi

VERSION="$(node -p "require('./frontend/src-tauri/tauri.conf.json').version")"
echo "▶ Movelo Studio $VERSION ($(git rev-parse --short HEAD)) for $TARGET"

(cd frontend && npm run build)
SKIP_FRONTEND_BUILD=1 VENV="${VENV:-venv_clean}" backend/build_sidecar.sh "$TARGET"

CONFIG='{"bundle":{"createUpdaterArtifacts":false,
  "resources":{"THIRD-PARTY-NOTICES.md":"THIRD-PARTY-NOTICES.md","backend/":"backend/",
               "../../vendor/fribidi/'$TARGET'/LICENSE.txt":"licenses/fribidi-LICENSE.txt",
               "../../vendor/fribidi/'$TARGET'/BUILD.txt":"licenses/fribidi-BUILD.txt"},
  "linux":{"deb":{"depends":["ffmpeg"]}}}}'

(cd frontend && npm run tauri build -- --target "$TARGET" --bundles deb,appimage --config "$CONFIG" "$@")

echo
echo "▶ Built:"
ls -1 "frontend/src-tauri/target/$TARGET/release/bundle/deb/"*.deb 2>/dev/null || true
ls -1 "frontend/src-tauri/target/$TARGET/release/bundle/appimage/"*.AppImage 2>/dev/null || true
