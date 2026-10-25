#!/usr/bin/env python3
"""How long the face step of character recognition would take on this Mac, and whether Movelo would use it.

Runs the app's own measurement (backend/app/services/characters/speed.py) with no saved answer, and prints it.
Run it from backend/, with the backend's Python:

  cd backend && venv/bin/python ../scripts/bench-characters.py

Needs the character models (scripts/fetch-models.sh) and ffmpeg (FFMPEG_PATH, or on PATH). Always exits 0 when it
could measure, even when the answer is "voices only"; a measurement that can't run exits 1 (CI treats that as a note)."""
import platform
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.services.characters import speed  # noqa: E402


def main() -> int:
    try:
        parts = speed.measure()
    except Exception as e:
        print(f"Could not measure: {type(e).__name__}: {e}")
        return 1
    estimate = parts.estimate
    measured = estimate <= speed.FACE_STEP_BUDGET_SECS
    faces = True if platform.machine() == "arm64" else measured   # as the app decides: Apple Silicon isn't measured
    print(f"Face step for a {speed.EPISODE_SECS} s episode ({speed.SPEECH_FRAMES} frames in lines, {speed.FACE_FRAMES} face-frames)")
    for name, secs in (("decode", parts.decode), ("YuNet", parts.yunet), ("Light-ASD", parts.light_asd),
                       ("SFace", parts.sface)):
        print(f"  {name:<10}{secs:7.1f} s")
    print(f"  {'raw total':<10}{parts.faces:7.1f} s")
    print(f"  {'calibrated':<10}{estimate:7.1f} s   (x{speed.CALIBRATION}; budget {speed.FACE_STEP_BUDGET_SECS} s)")
    print(f"  {'voice':<10}{parts.voice:7.1f} s   (not part of the total: voices always run)")
    print(f"Measured: {'faces on' if measured else 'voices only'}")
    note = "  (Apple Silicon: the app doesn't measure, faces are always on)" if platform.machine() == "arm64" else ""
    print(f"Decision: {'faces on' if faces else 'voices only'}{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
