"""
app/core/paths.py
Resolving stored media paths to real files.

Media locations are recorded in the database as whatever `UPLOAD_DIR` produced
at the time. That used to be a relative `./uploads`, so older rows hold paths
like `uploads/<project>/<job>/source.mp4`. Those only resolve when the process
runs with cwd=backend/ — true for `uvicorn` in development, false for the Tauri
sidecar, which is launched with an unrelated working directory. Exporting such
a job failed with "Error opening input file uploads/..." even though the file
was sitting safely on disk.

Everything that hands a stored path to ffmpeg (or opens it directly) should go
through `resolve_media_path`, which is cwd-independent and leaves already
absolute paths untouched.
"""
import logging
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)


def resolve_media_path(stored: str | None) -> str:
    """
    Map a stored media path onto a real file.

    Absolute paths are returned unchanged. Relative ones are tried against the
    upload root (both with and without a duplicated leading directory name) and
    finally the current working directory, which preserves the historical dev
    behaviour. If nothing matches, the original string is returned so callers
    still report the path the database actually holds.
    """
    if not stored:
        return stored or ""

    p = Path(stored)
    if p.is_absolute():
        return str(p)

    upload_root = Path(settings.UPLOAD_DIR).expanduser()
    if not upload_root.is_absolute():
        upload_root = (Path.cwd() / upload_root).resolve()

    candidates: list[Path] = []
    # "uploads/<project>/<job>/x" where upload_root itself ends in "uploads"
    if p.parts and p.parts[0] == upload_root.name:
        candidates.append(upload_root.joinpath(*p.parts[1:]))
    candidates.append(upload_root.parent / p)   # sibling-relative form
    candidates.append(upload_root / p)          # already relative to the root
    candidates.append(Path.cwd() / p)           # legacy cwd-relative form

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    logger.warning(
        "Could not resolve media path %r against upload root %s", stored, upload_root
    )
    return stored


def resolve_existing(stored: str | None) -> Path | None:
    """`resolve_media_path` as a Path, or None when the file genuinely isn't there."""
    if not stored:
        return None
    resolved = Path(resolve_media_path(stored))
    return resolved if resolved.exists() else None
