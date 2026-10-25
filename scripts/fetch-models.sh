#!/usr/bin/env bash
# Fetches the character-recognition models that are too big for git, into
# backend/app/assets/models/ (the folder the backend bundles whole):
#   scripts/fetch-models.sh
#
# The list, sizes and SHA-256s are in backend/app/assets/models/models.json.
# A file that is there and matches is left alone (the script returns at once,
# so the release workflow can cache the folder); a missing or wrong one is
# downloaded and checked. Any mismatch stops the script: a model that isn't the
# one we measured must never ship. Models without a URL are committed to git and
# are only checked.
#
# MODELS_DIR=<folder> checks/fetches there instead (for trying the script out).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIR="${MODELS_DIR:-$ROOT/backend/app/assets/models}"
MANIFEST="$ROOT/backend/app/assets/models/models.json"
mkdir -p "$DIR"

sha256_of() { shasum -a 256 "$1" | cut -d' ' -f1; }

python3 - "$MANIFEST" <<'PY' | while IFS=$'\t' read -r name file sha url; do
import json, sys
for e in json.load(open(sys.argv[1])):
    print("\t".join([e["name"], e["file"], e["sha256"], e["url"] or "-"]))
PY
  target="$DIR/$file"
  if [ -f "$target" ] && [ "$(sha256_of "$target")" = "$sha" ]; then
    echo "$name: ok"
    continue
  fi
  if [ "$url" = "-" ]; then
    echo "error: $file is committed to git but missing or changed in $DIR" >&2
    exit 1
  fi
  echo "$name: downloading $file"
  tmp="$(mktemp "$DIR/$file.XXXXXX")"
  curl -fsSL --retry 3 -o "$tmp" "$url" || { rm -f "$tmp"; echo "error: could not download $url" >&2; exit 1; }
  if [ "$(sha256_of "$tmp")" != "$sha" ]; then
    rm -f "$tmp"
    echo "error: $file downloaded from $url does not match the SHA-256 in models.json" >&2
    exit 1
  fi
  chmod 644 "$tmp"
  mv "$tmp" "$target"
  echo "$name: ok"
done
