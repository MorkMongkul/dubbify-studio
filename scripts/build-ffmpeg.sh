#!/usr/bin/env bash
# Builds the ffmpeg + ffprobe that the macOS app bundles, for one architecture:
#   scripts/build-ffmpeg.sh aarch64-apple-darwin   → vendor/ffmpeg/aarch64-apple-darwin/
#   scripts/build-ffmpeg.sh x86_64-apple-darwin    → vendor/ffmpeg/x86_64-apple-darwin/
# (Either works on an Apple Silicon Mac; Intel is cross-compiled.)
#
# Why build instead of downloading one: ready-made macOS builds include GPL
# libraries (x264, x265), which would put the whole app under GPL terms once
# it's given to anyone. This build uses only FFmpeg's own LGPL code plus
# Apple's frameworks — H.264 export goes through VideoToolbox, which the
# export already prefers. --disable-autodetect stops it from quietly linking
# Homebrew libraries, which would make the binary depend on files that only
# this Mac has. The result needs nothing but macOS itself.
#
# The source is the official release, pinned by SHA-256. LICENSE.txt (LGPL
# 2.1) and BUILD.txt (version, source URL, configure flags) are written next
# to the binaries; the release script bundles them into the app.
set -euo pipefail

FFMPEG_VERSION="8.1.1"
FFMPEG_SHA256="b6863adde98898f42602017462871b5f6333e65aec803fdd7a6308639c52edf3"
MIN_MACOS="11.0"

TARGET="${1:-aarch64-apple-darwin}"
case "$TARGET" in
  aarch64-apple-darwin) ARCH=arm64 ;;
  x86_64-apple-darwin)  ARCH=x86_64 ;;
  *) echo "Unsupported target: $TARGET" >&2; exit 1 ;;
esac

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/vendor/ffmpeg/$TARGET"
WORK="${FFMPEG_WORK_DIR:-${TMPDIR:-/tmp}/dubify-ffmpeg}"
SRC="$WORK/ffmpeg-$FFMPEG_VERSION-$TARGET"

if [ -x "$OUT/ffmpeg" ] && grep -q "^version: $FFMPEG_VERSION$" "$OUT/BUILD.txt" 2>/dev/null; then
  echo "ffmpeg $FFMPEG_VERSION for $TARGET is already built in $OUT"
  exit 0
fi

mkdir -p "$WORK"
TARBALL="$WORK/ffmpeg-$FFMPEG_VERSION.tar.xz"
if [ ! -f "$TARBALL" ]; then
  curl -fL --retry 3 -o "$TARBALL.part" "https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz"
  mv "$TARBALL.part" "$TARBALL"
fi
echo "$FFMPEG_SHA256  $TARBALL" | shasum -a 256 -c -

rm -rf "$SRC" && mkdir -p "$SRC"
tar -xf "$TARBALL" -C "$SRC" --strip-components 1
cd "$SRC"

FLAGS=(
  --arch="$ARCH" --cc="clang -arch $ARCH"
  --extra-cflags="-mmacosx-version-min=$MIN_MACOS"
  --extra-ldflags="-mmacosx-version-min=$MIN_MACOS"
  --disable-autodetect
  --enable-videotoolbox --enable-audiotoolbox --enable-zlib --enable-iconv
  --extra-libs=-liconv   # macOS keeps iconv in its own system library
  --disable-shared --enable-static
  --disable-doc --disable-ffplay --disable-debug --disable-network
)
if [ "$(uname -m)" != "$ARCH" ]; then
  FLAGS+=(--enable-cross-compile --target-os=darwin)
fi
if [ "$ARCH" = "x86_64" ] && ! command -v nasm >/dev/null 2>&1; then
  echo "note: nasm not found — building without x86 assembly (slower). Install with: brew install nasm" >&2
  FLAGS+=(--disable-x86asm)
fi

./configure "${FLAGS[@]}"
make -j"$(sysctl -n hw.ncpu)" ffmpeg ffprobe

# Portable means: only macOS's own libraries and frameworks.
for tool in ffmpeg ffprobe; do
  if otool -L "$tool" | tail -n +2 | grep -vE '^[[:space:]]+(/usr/lib/|/System/Library/)'; then
    echo "error: $tool links a library outside macOS (above) — it would not run on other Macs" >&2
    exit 1
  fi
done
if ./ffmpeg -hide_banner -buildconf | grep -q -- "--enable-gpl"; then
  echo "error: this build is GPL" >&2
  exit 1
fi

mkdir -p "$OUT"
cp ffmpeg ffprobe "$OUT/"
cp COPYING.LGPLv2.1 "$OUT/LICENSE.txt"
chmod 644 "$OUT/LICENSE.txt"
{
  echo "FFmpeg for Dubify Studio ($TARGET)"
  echo "version: $FFMPEG_VERSION"
  echo "license: GNU LGPL 2.1 or later (see LICENSE.txt)"
  echo "source: https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz (sha256 $FFMPEG_SHA256)"
  echo "built with: scripts/build-ffmpeg.sh"
  echo "configure: ${FLAGS[*]}"
} > "$OUT/BUILD.txt"

echo "Built LGPL ffmpeg $FFMPEG_VERSION for $TARGET → $OUT ($(lipo -archs "$OUT/ffmpeg"))"
