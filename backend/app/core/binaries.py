"""
app/core/binaries.py
Where ffmpeg and ffprobe are. Every subprocess call goes through ffmpeg() /
ffprobe() rather than a bare program name.

Order: an explicit path (FFMPEG_PATH / FFPROBE_PATH — the desktop app points
these at the copies bundled inside it) → PATH → the usual Homebrew folders.
The last step matters on macOS: an app opened from Finder or the Dock gets
PATH=/usr/bin:/bin:/usr/sbin:/sbin, which doesn't include Homebrew, so a bare
"ffmpeg" fails even on a Mac that has it installed.
"""
import logging
import os
import shutil
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

EXTRA_SEARCH_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")


def find(name: str) -> Optional[str]:
    """Absolute path of `name` ("ffmpeg" / "ffprobe"), or None if it isn't installed."""
    override = getattr(settings, f"{name.upper()}_PATH", "")
    if override:
        if os.path.isfile(override) and os.access(override, os.X_OK):
            return override
        logger.warning(f"{name.upper()}_PATH={override!r} is not an executable file — searching instead")
    search = os.pathsep.join([os.environ.get("PATH", ""), *EXTRA_SEARCH_DIRS])
    return shutil.which(name, path=search)


def ffmpeg() -> str:
    return find("ffmpeg") or "ffmpeg"


def ffprobe() -> str:
    return find("ffprobe") or "ffprobe"
