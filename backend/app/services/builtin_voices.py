"""
app/services/builtin_voices.py
Movelo Studio's built-in voices: a library of VoxCPM2 voices that ships with
the app, ready to use without designing or cloning anything.

Each one is a reference clip + its exact transcript, used like any ultimate
(reference + transcript) voice, so every engine path works unchanged. The clips
live in app/assets/builtin_voices/ (lossless FLAC; manifest.json lists them,
built from the casting sessions) and are copied at startup into the voices
folder (UPLOAD_DIR/voices/<id>/reference.wav) — a stable, writable location
that follows the data folder, unlike the app bundle.

Built-in voices have fixed ids ("builtin-…") so speakers and lines that use
them keep them across updates. Their names and tags (who they sound like,
personality, role) also come from the manifest, so an update can correct them. They can't be renamed, edited or deleted; a new
app version updates or adds them on its first launch. A re-recorded voice gets
a new id (its installed clip is never replaced), so lines voiced with the old
one stay consistent.
"""
import json
import logging
from pathlib import Path

import soundfile as sf
from sqlalchemy import select

from app.core.config import settings
from app.models.models import Voice, VoiceMode

logger = logging.getLogger(__name__)

PREFIX = "builtin-"
ASSETS = Path(__file__).resolve().parent.parent / "assets" / "builtin_voices"


def is_builtin(voice_id: str) -> bool:
    return (voice_id or "").startswith(PREFIX)


def load_manifest(assets: Path = ASSETS) -> dict:
    path = assets / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"voices": []}


def _install_clip(src: Path, dest: Path) -> None:
    """FLAC in the app → WAV in the voices folder (what every engine accepts)."""
    audio, rate = sf.read(str(src), dtype="int16")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp.wav")
    sf.write(str(tmp), audio, rate, subtype="PCM_16")
    tmp.replace(dest)


async def seed_builtin_voices(assets: Path = ASSETS) -> int:
    """
    Make the database and voices folder match the manifest: add missing
    built-in voices, refresh changed ones, and restore a missing clip. Voices
    a newer manifest no longer lists are kept (lines may still use them).
    Returns how many voices were added or changed.
    """
    from app.core.database import AsyncSessionLocal

    manifest = load_manifest(assets)
    params = manifest.get("settings", {})
    voices_dir = Path(settings.UPLOAD_DIR) / "voices"
    changed = 0
    async with AsyncSessionLocal() as db:
        existing = {
            v.id: v for v in (await db.execute(select(Voice).where(Voice.id.like(f"{PREFIX}%")))).scalars()
        }
        for entry in manifest.get("voices", []):
            clip = voices_dir / entry["id"] / "reference.wav"
            if not clip.exists():
                _install_clip(assets / entry["file"], clip)
            wanted = {
                "name": entry["name"],
                "mode": VoiceMode.ULTIMATE.value,
                "description": entry["description"],
                "reference_audio_path": str(clip),
                "reference_transcript": entry["transcript"],
                "cfg_value": float(params.get("cfg_value", 2.8)),
                "inference_timesteps": int(params.get("inference_timesteps", 15)),
                "seed": -1,
                # Tags pick the voice's section in the Voices tab (voice_tags.py)
                "gender": entry.get("gender"),
                "age_group": entry.get("age_group"),
                "personality": list(entry.get("personality") or []),
                "roles": list(entry.get("roles") or []),
            }
            voice = existing.get(entry["id"])
            if voice is None:
                db.add(Voice(id=entry["id"], **wanted))
                changed += 1
            elif any(getattr(voice, k) != v for k, v in wanted.items()):
                for k, v in wanted.items():
                    setattr(voice, k, v)
                changed += 1
        await db.commit()
    if changed:
        logger.info(f"Built-in voices: {changed} added or updated ({len(manifest.get('voices', []))} in the library)")
    return changed
