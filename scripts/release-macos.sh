#!/usr/bin/env bash
# Builds the macOS desktop app for one chip architecture:
#   scripts/release-macos.sh aarch64-apple-darwin                             # Apple Silicon
#   VENV=venv_clean_x86_64 scripts/release-macos.sh x86_64-apple-darwin       # Intel
# (VENV is the backend build venv — see backend/build_sidecar.sh.)
#
# Steps: frontend → backend binary (embeds the frontend) → optional bundled
# ffmpeg → Tauri bundle (.app and .dmg) under
# frontend/src-tauri/target/<target>/release/bundle/.
#
# Bundled ffmpeg: put builds for this architecture at
# vendor/ffmpeg/<target>/ffmpeg and .../ffprobe (not committed). Without them
# the app uses an ffmpeg installed on the Mac (e.g. Homebrew) and tells the
# user in Settings when there is none.
#
# Signing and notarization — needed so other Macs open the app without a
# Gatekeeper block — happen automatically when these are set:
#   APPLE_SIGNING_IDENTITY   "Developer ID Application: <Name> (<TEAMID>)"
#   APPLE_ID, APPLE_PASSWORD (an app-specific password), APPLE_TEAM_ID
#     — or APPLE_API_KEY, APPLE_API_ISSUER, APPLE_API_KEY_PATH
# Extra flags after the target go to `tauri build` (e.g. --bundles app).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${1:-aarch64-apple-darwin}"
shift || true
cd "$ROOT"

if [ -n "$(git status --porcelain)" ]; then
  echo "warning: uncommitted changes — this build won't match any commit." >&2
fi
VERSION="$(node -p "require('./frontend/src-tauri/tauri.conf.json').version")"
echo "▶ Dubify Studio $VERSION ($(git rev-parse --short HEAD)) for $TARGET"

(cd frontend && npm run build)
SKIP_FRONTEND_BUILD=1 VENV="${VENV:-venv_clean}" backend/build_sidecar.sh "$TARGET"

BIN_DIR=frontend/src-tauri/binaries
EXTERNAL='["binaries/dubify-backend"]'
if [ -x "vendor/ffmpeg/$TARGET/ffmpeg" ] && [ -x "vendor/ffmpeg/$TARGET/ffprobe" ]; then
  cp "vendor/ffmpeg/$TARGET/ffmpeg" "$BIN_DIR/ffmpeg-$TARGET"
  cp "vendor/ffmpeg/$TARGET/ffprobe" "$BIN_DIR/ffprobe-$TARGET"
  EXTERNAL='["binaries/dubify-backend","binaries/ffmpeg","binaries/ffprobe"]'
  echo "▶ Bundling ffmpeg from vendor/ffmpeg/$TARGET"
else
  echo "note: no vendor/ffmpeg/$TARGET — the app will use the Mac's own ffmpeg." >&2
fi

if [ -z "${APPLE_SIGNING_IDENTITY:-}" ]; then
  echo "note: APPLE_SIGNING_IDENTITY is not set — the app won't be signed, so other Macs will block it." >&2
fi

(cd frontend && npm run tauri build -- --target "$TARGET" \
  --config "{\"bundle\":{\"externalBin\":$EXTERNAL}}" "$@")

echo
echo "▶ Built:"
ls -1d "frontend/src-tauri/target/$TARGET/release/bundle/macos/"*.app 2>/dev/null || true
ls -1 "frontend/src-tauri/target/$TARGET/release/bundle/dmg/"*.dmg 2>/dev/null || true
