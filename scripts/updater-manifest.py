#!/usr/bin/env python3
"""Writes latest.json, which installed copies of the app read to find updates.

    scripts/updater-manifest.py v0.1.1 dist > dist/latest.json

The app asks for releases/latest/download/latest.json (see "plugins.updater"
in frontend/src-tauri/tauri.conf.json). For each chip it lists where the
signed update package is and its signature, which the app checks against its
built-in public key before installing.
"""
import datetime
import json
import os
import pathlib
import sys

# Tauri's platform name → the update package the release job publishes for it.
PACKAGES = {
    "darwin-aarch64": "Dubify-Studio-Apple-Silicon.app.tar.gz",
    "darwin-x86_64": "Dubify-Studio-Intel.app.tar.gz",
}


def main() -> None:
    tag, dist = sys.argv[1], pathlib.Path(sys.argv[2])
    repo = os.environ.get("GITHUB_REPOSITORY", "MorkMongkul/dubbify-studio")
    base = f"https://github.com/{repo}/releases/download/{tag}"

    platforms = {}
    for platform, name in PACKAGES.items():
        package, signature = dist / name, dist / f"{name}.sig"
        if not package.is_file() or not signature.is_file():
            sys.exit(f"error: {package} or its .sig is missing")
        platforms[platform] = {"signature": signature.read_text().strip(), "url": f"{base}/{name}"}

    manifest = {
        "version": tag.removeprefix("v"),
        "notes": f"https://github.com/{repo}/releases/tag/{tag}",
        "pub_date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platforms": platforms,
    }
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
