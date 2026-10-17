"""
app/core/data_folder.py
Following the app-data folder when the desktop app moves it.

Media locations are stored as absolute paths inside the app-data folder
(~/Library/Application Support/<app id>/uploads/...). Renaming Dubify Studio
to Movelo Studio changed the app ID, so the desktop app moves the folder to
its new name on first launch and tells the backend where it used to be
(DUBIFY_PREVIOUS_DATA_DIR). Every stored path inside the old folder is then
rewritten to the new one.

Only values that start with the old folder are changed, so running it again
changes nothing. `resolve_media_path` (app/core/paths.py) and the link the app
leaves at the old location still cover anything missed here.
"""
import logging
from pathlib import Path

from sqlalchemy import func, literal, select, update

from app.core.database import AsyncSessionLocal
from app.models.models import Job, Overlay, OverlayTemplate, Project, Segment, Speaker, Voice

logger = logging.getLogger(__name__)

PATH_COLUMNS = (
    Voice.reference_audio_path,
    Project.logo_path,
    Job.video_path,
    Job.audio_path,
    Job.output_path,
    Job.subtitle_path,
    Speaker.reference_audio_path,
    Segment.tts_audio_path,
    Overlay.media_path,
)


def _folder_prefix(folder: str) -> str:
    return str(Path(folder)).rstrip("/") + "/" if folder else ""


def _moved(value, old: str, new: str, is_path: bool = False):
    """
    `value` (JSON) with paths inside `old` moved to `new`. Only `*_path`
    fields count as paths: a subtitle's text that mentions the folder stays as typed.
    """
    if isinstance(value, str):
        return new + value[len(old):] if is_path and value.startswith(old) else value
    if isinstance(value, list):
        return [_moved(v, old, new, is_path) for v in value]
    if isinstance(value, dict):
        return {k: _moved(v, old, new, k.endswith("_path")) for k, v in value.items()}
    return value


async def follow_moved_data_folder(previous: str, current: str, *, session_factory=AsyncSessionLocal) -> int:
    """Rewrite stored paths inside `previous` to `current`. Returns how many values changed."""
    old, new = _folder_prefix(previous), _folder_prefix(current)
    if not old or not new or old == new:
        return 0

    changed = 0
    async with session_factory() as db:
        for column in PATH_COLUMNS:
            # substr/length count characters in both SQLite and Postgres, so a
            # home folder with non-ASCII letters is matched exactly (LIKE would
            # treat "_" and "%" in folder names as wildcards).
            result = await db.execute(
                update(column.class_)
                .where(func.substr(column, 1, len(old)) == old)
                .values({column.key: literal(new) + func.substr(column, len(old) + 1)})
                .execution_options(synchronize_session=False)
            )
            changed += result.rowcount or 0

        for template in (await db.execute(select(OverlayTemplate))).scalars():
            items = _moved(template.items, old, new)
            if items != template.items:
                template.items = items
                changed += 1
        await db.commit()

    if changed:
        logger.info(f"Data folder moved from {previous} to {current}: updated {changed} stored file locations.")
    return changed
