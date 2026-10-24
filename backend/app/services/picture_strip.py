"""
app/services/picture_strip.py
The picture strip (0.3.2): small frames of the episode's video in a row above
the timeline's tracks, to find scenes — for looking only. Made in the
background with the bundled ffmpeg (fps + scale + the mjpeg encoder, all in
the LGPL build) into `<job>/frames/`, with `done.json` written last, so a
strip that exists is complete. Never blocks the editor.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import shutil
from pathlib import Path
from uuid import uuid4

from app.core.binaries import ffmpeg

logger = logging.getLogger(__name__)

MAX_FRAMES = 600
FRAME_HEIGHT = 72
DONE = "done.json"


def frame_interval(duration: float) -> float:
    """Seconds between frames: one a second, fewer (in half-second steps) for long videos."""
    return max(1.0, math.ceil((duration or 0) / MAX_FRAMES * 2) / 2)


def frames_state(folder: Path) -> dict | None:
    """{interval, count} of a finished strip, else None."""
    try:
        return json.loads((Path(folder) / DONE).read_text())
    except (OSError, ValueError):
        return None


async def make_frames(video: str, folder: Path, interval: float) -> int:
    """Make the strip into a folder of its own, swapped in when complete. Returns the frame count."""
    folder = Path(folder)
    tmp = folder.parent / f".frames_{uuid4().hex}"
    tmp.mkdir(parents=True)
    try:
        process = await asyncio.create_subprocess_exec(
            ffmpeg(), "-v", "error", "-y", "-i", video,
            "-vf", f"fps=1/{interval},scale=-2:{FRAME_HEIGHT}", "-q:v", "6", str(tmp / "f_%05d.jpg"),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await process.communicate()
        count = len(list(tmp.glob("f_*.jpg")))
        if process.returncode != 0 or count == 0:
            raise RuntimeError(f"ffmpeg couldn't make the frames: {err.decode(errors='replace')[-300:]}")
        (tmp / DONE).write_text(json.dumps({"interval": interval, "count": count}))
        shutil.rmtree(folder, ignore_errors=True)
        tmp.rename(folder)
        return count
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def make_frames_quietly(video: str, folder: Path, interval: float) -> None:
    """For a background task: a strip that can't be made is just not shown."""
    try:
        await make_frames(video, folder, interval)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"No picture strip for {video}: {e}")
