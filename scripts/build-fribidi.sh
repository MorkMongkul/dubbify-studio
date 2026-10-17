#!/usr/bin/env bash
# Builds the FriBiDi library the backend bundles, for one architecture:
#   scripts/build-fribidi.sh aarch64-apple-darwin   → vendor/fribidi/aarch64-apple-darwin/
#   scripts/build-fribidi.sh x86_64-apple-darwin    → vendor/fribidi/x86_64-apple-darwin/
# (Either works on an Apple Silicon Mac; Intel is cross-compiled.)
#
# Why: burned-in subtitles are drawn with Pillow, which shapes Khmer
# (stacked consonants, vowels placed around them) only through its "raqm"
# layout, and that needs FriBiDi at runtime. Pillow's wheels don't include
# it; it loads libfribidi.dylib if it can find one, and otherwise silently
# draws Khmer unshaped. backend/build_sidecar.sh puts this library next to
# the backend's own libraries, where Pillow finds it on every Mac.
#
# The source is the official release, pinned by SHA-256, built for macOS 11+
# (Homebrew's copy requires the Mac it was built on). LICENSE.txt (LGPL 2.1)
# and BUILD.txt are written next to the library; the release script bundles
# them, and the release workflow publishes the source.
set -euo pipefail

FRIBIDI_VERSION="1.0.16"
FRIBIDI_SHA256="1b1cde5b235d40479e91be2f0e88a309e3214c8ab470ec8a2744d82a5a9ea05c"
MIN_MACOS="11.0"

TARGET="${1:-aarch64-apple-darwin}"
case "$TARGET" in
  aarch64-apple-darwin) ARCH=arm64 ;;
  x86_64-apple-darwin)  ARCH=x86_64 ;;
  *) echo "Unsupported target: $TARGET" >&2; exit 1 ;;
esac

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/vendor/fribidi/$TARGET"
WORK="${FRIBIDI_WORK_DIR:-${TMPDIR:-/tmp}/movelo-fribidi}"
SRC="$WORK/fribidi-$FRIBIDI_VERSION-$TARGET"

if [ -f "$OUT/libfribidi.dylib" ] && grep -q "^version: $FRIBIDI_VERSION$" "$OUT/BUILD.txt" 2>/dev/null; then
  echo "FriBiDi $FRIBIDI_VERSION for $TARGET is already built in $OUT"
  exit 0
fi

mkdir -p "$WORK"
TARBALL="$WORK/fribidi-$FRIBIDI_VERSION.tar.xz"
if [ ! -f "$TARBALL" ]; then
  curl -fL --retry 3 -o "$TARBALL.part" \
    "https://github.com/fribidi/fribidi/releases/download/v$FRIBIDI_VERSION/fribidi-$FRIBIDI_VERSION.tar.xz"
  mv "$TARBALL.part" "$TARBALL"
fi
echo "$FRIBIDI_SHA256  $TARBALL" | shasum -a 256 -c -

rm -rf "$SRC" && mkdir -p "$SRC"
tar -xf "$TARBALL" -C "$SRC" --strip-components 1
cd "$SRC"

export CC="clang -arch $ARCH"
export CFLAGS="-O2 -mmacosx-version-min=$MIN_MACOS"
export LDFLAGS="-mmacosx-version-min=$MIN_MACOS"
FLAGS=(--prefix="$SRC/_prefix" --enable-shared --disable-static --disable-dependency-tracking)
if [ "$(uname -m)" != "$ARCH" ]; then
  FLAGS+=(--host="$ARCH-apple-darwin")
fi
./configure "${FLAGS[@]}"
make -j"$(sysctl -n hw.ncpu)" -C lib
make -C lib install

mkdir -p "$OUT"
cp "$SRC/_prefix/lib/libfribidi.0.dylib" "$OUT/libfribidi.dylib"
# Pillow asks for "libfribidi.dylib" by name; the backend finds it next to its
# own libraries. Changing the id invalidates the signature — sign it again.
install_name_tool -id "@rpath/libfribidi.dylib" "$OUT/libfribidi.dylib"
codesign --force --sign - "$OUT/libfribidi.dylib"

# Portable means: only macOS's own libraries.
if otool -L "$OUT/libfribidi.dylib" | tail -n +3 | grep -vE '^[[:space:]]+/usr/lib/'; then
  echo "error: libfribidi links a library outside macOS (above)" >&2
  exit 1
fi
cp COPYING "$OUT/LICENSE.txt"
chmod 644 "$OUT/LICENSE.txt"
{
  echo "FriBiDi for Movelo Studio ($TARGET)"
  echo "version: $FRIBIDI_VERSION"
  echo "license: GNU LGPL 2.1 or later (see LICENSE.txt)"
  echo "source: https://github.com/fribidi/fribidi/releases/download/v$FRIBIDI_VERSION/fribidi-$FRIBIDI_VERSION.tar.xz (sha256 $FRIBIDI_SHA256)"
  echo "built with: scripts/build-fribidi.sh"
  echo "configure: ${FLAGS[*]} (macOS $MIN_MACOS+)"
} > "$OUT/BUILD.txt"

echo "Built FriBiDi $FRIBIDI_VERSION for $TARGET → $OUT ($(lipo -archs "$OUT/libfribidi.dylib"))"
