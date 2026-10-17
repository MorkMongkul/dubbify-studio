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
# Bundled ffmpeg: build it once per architecture with scripts/build-ffmpeg.sh
# (LGPL, no GPL parts) into vendor/ffmpeg/<target>/ (not committed). Without
# it the app relies on an ffmpeg installed on the Mac (e.g. Homebrew) and says
# so in Settings when there is none.
#
# Signing and notarization — needed so other Macs open the app without a
# Gatekeeper prompt — happen automatically when these are set (otherwise the
# app is signed ad-hoc and users allow it once in Privacy & Security):
#   APPLE_SIGNING_IDENTITY   "Developer ID Application: <Name> (<TEAMID>)"
#   APPLE_ID, APPLE_PASSWORD (an app-specific password), APPLE_TEAM_ID
#     — or APPLE_API_KEY, APPLE_API_ISSUER, APPLE_API_KEY_PATH
#
# In-app updates: with TAURI_SIGNING_PRIVATE_KEY (and its _PASSWORD) set, the
# build also makes a signed update package ("Movelo Studio.app.tar.gz" + .sig)
# that installed copies accept. CI requires it; a local build without it just
# leaves the update package out.
# Extra flags after the target go to `tauri build` (e.g. --bundles app).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${1:-aarch64-apple-darwin}"
shift || true
cd "$ROOT"

# CI passes secrets that don't exist as empty strings, but Tauri treats a
# variable that merely exists as a credential: it would import an empty
# certificate and notarize with an empty Apple ID, failing the build. Only
# non-empty ones count.
for var in APPLE_CERTIFICATE APPLE_CERTIFICATE_PASSWORD APPLE_SIGNING_IDENTITY \
           APPLE_ID APPLE_PASSWORD APPLE_TEAM_ID APPLE_API_KEY APPLE_API_ISSUER APPLE_API_KEY_PATH \
           TAURI_SIGNING_PRIVATE_KEY; do
  if [ -z "${!var:-}" ]; then unset "$var"; fi
done

if [ -n "$(git status --porcelain)" ]; then
  echo "warning: uncommitted changes — this build won't match any commit." >&2
fi
VERSION="$(node -p "require('./frontend/src-tauri/tauri.conf.json').version")"
echo "▶ Movelo Studio $VERSION ($(git rev-parse --short HEAD)) for $TARGET"

(cd frontend && npm run build)
SKIP_FRONTEND_BUILD=1 VENV="${VENV:-venv_clean}" backend/build_sidecar.sh "$TARGET"

BIN_DIR=frontend/src-tauri/binaries
EXTRA_CONFIG='{"bundle":{"externalBin":["binaries/movelo-backend"]}}'
if [ -x "vendor/ffmpeg/$TARGET/ffmpeg" ] && [ -x "vendor/ffmpeg/$TARGET/ffprobe" ]; then
  cp "vendor/ffmpeg/$TARGET/ffmpeg" "$BIN_DIR/ffmpeg-$TARGET"
  cp "vendor/ffmpeg/$TARGET/ffprobe" "$BIN_DIR/ffprobe-$TARGET"
  # The LGPL requires its text (and the build details) to ship with the binaries.
  EXTRA_CONFIG="{\"bundle\":{\"externalBin\":[\"binaries/movelo-backend\",\"binaries/ffmpeg\",\"binaries/ffprobe\"],\"resources\":{\"THIRD-PARTY-NOTICES.md\":\"THIRD-PARTY-NOTICES.md\",\"../../vendor/ffmpeg/$TARGET/LICENSE.txt\":\"licenses/ffmpeg-LICENSE.txt\",\"../../vendor/ffmpeg/$TARGET/BUILD.txt\":\"licenses/ffmpeg-BUILD.txt\"}}}"
  echo "▶ Bundling ffmpeg from vendor/ffmpeg/$TARGET (build it with scripts/build-ffmpeg.sh)"
else
  echo "note: no vendor/ffmpeg/$TARGET — the app will rely on the Mac's own ffmpeg." >&2
  echo "      Build a bundleable one with: scripts/build-ffmpeg.sh $TARGET" >&2
fi

if [ -z "${APPLE_SIGNING_IDENTITY:-}" ]; then
  # Ad-hoc signature ("-"): Apple Silicon refuses to run downloaded apps that
  # aren't signed at all ("damaged"). Without a Developer ID, users still have
  # to allow the app once: System Settings → Privacy & Security → Open Anyway.
  export APPLE_SIGNING_IDENTITY="-"
  echo "note: no Developer ID (APPLE_SIGNING_IDENTITY) — signing ad-hoc; other Macs must allow the app once." >&2
fi

TAURI_ARGS=(--target "$TARGET" --config "$EXTRA_CONFIG")
if [ -z "${TAURI_SIGNING_PRIVATE_KEY:-}" ]; then
  if [ -n "${CI:-}" ]; then
    echo "error: TAURI_SIGNING_PRIVATE_KEY is not set — installed apps couldn't update to this build." >&2
    exit 1
  fi
  echo "note: no TAURI_SIGNING_PRIVATE_KEY — building without an update package." >&2
  TAURI_ARGS+=(--config '{"bundle":{"createUpdaterArtifacts":false}}')
fi

(cd frontend && npm run tauri build -- "${TAURI_ARGS[@]}" "$@")

echo
echo "▶ Built:"
ls -1d "frontend/src-tauri/target/$TARGET/release/bundle/macos/"*.app 2>/dev/null || true
ls -1 "frontend/src-tauri/target/$TARGET/release/bundle/dmg/"*.dmg 2>/dev/null || true
ls -1 "frontend/src-tauri/target/$TARGET/release/bundle/macos/"*.app.tar.gz* 2>/dev/null || true
