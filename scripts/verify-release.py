#!/usr/bin/env python3
"""Checks a draft release's files before the release is published.

    gh release download v0.2.7 -D <folder> -p latest.json -p 'Movelo-Studio-*'
    python3 -I scripts/verify-release.py <folder> 0.2.7 [--only-arm] [--no-self-test]

- the asset names the website and the updater rely on;
- the update feed (latest.json): its version, its URLs, and each signature
  equal to the matching .sig file;
- every update package's minisign signature (and its trusted comment) against
  the public key in frontend/src-tauri/tauri.conf.json;
- inside each update package: the app's version and chip, and the backend's
  --self-test (it loads, shapes Khmer, finds its built-in voices). On Apple
  Silicon the Intel one runs under Rosetta. --no-self-test leaves it out: the
  release job (Linux) runs this script that way, after each build job ran the
  self-test from its own update package on its own chip.

Needs the `cryptography` package (`pip install cryptography`); the backend's
venv doesn't have it.
"""
import base64
import hashlib
import json
import plistlib
import platform
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

folder, version = Path(sys.argv[1]), sys.argv[2]
chips = [("darwin-aarch64", "Apple-Silicon")] if "--only-arm" in sys.argv else [
    ("darwin-aarch64", "Apple-Silicon"), ("darwin-x86_64", "Intel")]
self_test = "--no-self-test" not in sys.argv
CPU = {"Apple-Silicon": 0x0100000C, "Intel": 0x01000007}     # Mach-O cputype: arm64, x86_64

conf = json.loads((Path(__file__).resolve().parent.parent / "frontend/src-tauri/tauri.conf.json").read_text())
pub = base64.b64decode(base64.b64decode(conf["plugins"]["updater"]["pubkey"]).decode().splitlines()[1])
key = Ed25519PublicKey.from_public_bytes(pub[10:])          # 2 bytes algorithm, 8 key id, 32 key


def verify(data: bytes, sig_b64: str) -> str:
    lines = base64.b64decode(sig_b64).decode().splitlines()
    sig = base64.b64decode(lines[1])                          # 2 algorithm, 8 key id, 64 signature
    assert sig[2:10] == pub[2:10], "signed with a different key"
    key.verify(sig[10:], hashlib.blake2b(data).digest() if sig[:2] == b"ED" else data)
    trusted = lines[2].split(":", 1)[1].strip()
    key.verify(base64.b64decode(lines[3]), sig[10:] + trusted.encode())   # the trusted comment too
    return trusted


def check_inside(package: Path, chip: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        out, home = Path(tmp) / "app", Path(tmp) / "home"
        out.mkdir(); home.mkdir()
        subprocess.run(["tar", "-xzf", str(package), "-C", str(out)], check=True)
        app, = out.glob("*.app")
        info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
        assert info["CFBundleShortVersionString"] == version, f"{chip}: the app inside is {info['CFBundleShortVersionString']}"
        magic, cpu = struct.unpack("<II", (app / "Contents/MacOS" / info["CFBundleExecutable"]).read_bytes()[:8])
        assert magic == 0xFEEDFACF and cpu == CPU[chip], f"{chip}: the app inside is built for another chip"
        if not self_test:
            return "left to the build"
        cmd = [str(app / "Contents/Resources/backend/movelo-backend"), "--self-test"]
        if chip == "Intel" and platform.machine() == "arm64":
            cmd = ["/usr/bin/arch", "-x86_64", *cmd]
        r = subprocess.run(cmd, env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
                           capture_output=True, text=True, timeout=300)
        assert r.returncode == 0 and "Khmer text shaping: ok" in r.stdout, f"{chip} self-test:\n{r.stdout}{r.stderr}"
        return "; ".join(line for line in r.stdout.splitlines() if line.strip())


feed = json.loads((folder / "latest.json").read_text())
assert feed["version"] == version, f"latest.json says {feed['version']}"
for platform_name, chip in chips:
    name = f"Movelo-Studio-{chip}.app.tar.gz"
    for asset in (f"Movelo-Studio-{chip}.dmg", name, f"{name}.sig"):
        assert (folder / asset).is_file(), f"missing {asset}"
    entry = feed["platforms"][platform_name]
    assert entry["url"].endswith(f"/v{version}/{name}"), entry["url"]
    sig_file = (folder / f"{name}.sig").read_text().strip()
    assert entry["signature"] == sig_file, f"{platform_name}: latest.json's signature isn't {name}.sig"
    print(f"{platform_name}: {name} signature OK ({verify((folder / name).read_bytes(), sig_file)})")
    print(f"{platform_name}: inside, version {version} for {chip}; self-test: {check_inside(folder / name, chip)}")
print(f"release {version}: names, feed, signatures and both apps OK")
