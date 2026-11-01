"""Models the packaged app downloads once into its data folder (separation, transcription, voice kinds): each file
from a pinned revision, its SHA-256 checked before it's kept. Never in the app itself, and never in models.json
(that list is the voice prints' version: a change makes every line be heard again)."""
import hashlib
import logging
import os
from pathlib import Path

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


def ready(folder: Path | None, files: dict[str, str]) -> bool:
    return folder is not None and all((folder / name).is_file() for name in files)


async def download(folder: Path, base_url: str, files: dict[str, str], what: str) -> bool:
    """Downloads the files of `files` (name → SHA-256; a name may hold a sub-folder) that aren't in `folder` yet.
    True when all are there. Never raises: a failed download leaves nothing behind and is tried again at the next
    start-up — except one whose SHA-256 doesn't match, which this app version never downloads again (the file
    upstream changed: `<folder>.mismatch` holds the version)."""
    mismatch = folder.parent / f"{folder.name}.mismatch"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if mismatch.is_file() and mismatch.read_text().strip() == settings.version:
            return False
        async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30.0, read=120.0)) as client:
            for name, sha256 in files.items():
                target = folder / name
                if target.is_file():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                part = target.with_name(target.name + ".part")
                try:
                    digest = hashlib.sha256()
                    async with client.stream("GET", f"{base_url}/{name}") as response:
                        response.raise_for_status()
                        with open(part, "wb") as out:
                            async for chunk in response.aiter_bytes(1 << 20):
                                digest.update(chunk)
                                out.write(chunk)
                    if digest.hexdigest() != sha256:
                        mismatch.write_text(settings.version)
                        raise ValueError(f"{name}'s SHA-256 is {digest.hexdigest()}, expected {sha256}")
                    os.replace(part, target)
                finally:
                    part.unlink(missing_ok=True)
        logger.info(f"{what}: ready in {folder}")
        return True
    except Exception as e:   # noqa: BLE001 — offline, a full disk: whatever needs it falls back meanwhile
        logger.warning(f"{what}: couldn't download the model: {e}")
        return False
