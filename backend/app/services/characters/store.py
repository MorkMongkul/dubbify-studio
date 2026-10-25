"""Characters in the database: which lines are confirmed, the profiles they make, the prints kept per line, and
recognising an episode's lines before translation.

A profile is recomputed from the stored prints of a character's confirmed lines every time, never kept as a running
sum, so a later correction takes effect at once. The models run only in `recognise`, imported there and with no
session open (SQLite has one writer), so a Mac that can't load them still analyses as before."""
import asyncio
import hashlib
import json
import logging
from functools import lru_cache
from pathlib import Path

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.paths import resolve_media_path
from app.models.models import Job, LinePrint, Segment, Speaker
from app.services.characters import models
from app.services.characters.decide import Profile, decide, profile_from
from app.services.characters.types import LinePrints
from app.services.timeline import actor_span

logger = logging.getLogger(__name__)

VOICE_SIZE = 192
FACE_SIZE = 128


@lru_cache(maxsize=1)
def model_version() -> str:
    """Which models made a print: the first 12 hex of models.json's SHA-256, so any model change makes new prints."""
    return hashlib.sha256(models.MANIFEST.read_bytes()).hexdigest()[:12]


def __getattr__(name: str):
    # MODEL_VERSION reads models.json only when someone asks for it
    if name == "MODEL_VERSION":
        return model_version()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def is_confirmed(seg: Segment, job: Job) -> bool:
    """The user picked the line's character, or nobody marked it to check and it was voiced, approved or is in a
    finished episode. A line to check never counts, so a wrong guess can't teach itself."""
    return bool(seg.who_confirmed) or (
        seg.who_check is None and bool(seg.tts_audio_path or seg.is_approved or job.finished_at))


def who_check_json(check: list[dict] | None) -> str | None:
    """segments.who_check as stored: None when sure, else the guesses (possibly none) as JSON."""
    return None if check is None else json.dumps(check)


def faces_dir(job: Job) -> Path:
    """Where a job's face crops go (one JPEG per line, named after it)."""
    return Path(settings.UPLOAD_DIR) / job.project_id / job.id / "faces"


def _vector(blob: bytes | None, size: int) -> np.ndarray | None:
    return np.frombuffer(blob, np.float32) if blob and len(blob) == size * 4 else None


def _blob(vector: np.ndarray | None) -> bytes | None:
    return None if vector is None else np.asarray(vector, np.float32).tobytes()


async def project_profiles(db: AsyncSession, project_id: str, exclude_job_id: str | None = None) -> dict[str, Profile]:
    """{speaker id: profile} from the stored prints of each character's confirmed lines (made by today's models).
    Characters with no such print are left out."""
    query = (
        select(Segment, Job, LinePrint)
        .join(Job, Job.id == Segment.job_id)
        .join(LinePrint, LinePrint.segment_id == Segment.id)
        .join(Speaker, Speaker.id == Segment.speaker_id)
        .where(Job.project_id == project_id, Speaker.project_id == project_id,
               LinePrint.model_version == model_version())
    )
    if exclude_job_id:
        query = query.where(Job.id != exclude_job_id)
    by_speaker: dict[str, list[LinePrints]] = {}
    for seg, job, row in (await db.execute(query)).all():
        if is_confirmed(seg, job):
            by_speaker.setdefault(seg.speaker_id, []).append(LinePrints(
                _vector(row.voice_print, VOICE_SIZE), _vector(row.face_print, FACE_SIZE), row.speaking_score, None))
    profiles = {speaker_id: profile_from(prints) for speaker_id, prints in by_speaker.items()}
    return {sid: p for sid, p in profiles.items() if p.voice is not None or p.face is not None}


async def save_prints(db: AsyncSession, job_id: str, segment_ids: list[str], prints: list[LinePrints],
                      crops_dir: Path) -> None:
    """Keep each line's prints, replacing any it had, and its speaking face as crops_dir/<segment id>.jpg.
    Flushes; the caller commits (with the lines, so prints never outlive a failed swap)."""
    pairs = list(zip(segment_ids, prints))
    if not pairs:
        return
    await db.execute(delete(LinePrint).where(LinePrint.segment_id.in_([sid for sid, _ in pairs])))
    rows = []
    for segment_id, p in pairs:
        crop = None
        if p.face_crop:
            crops_dir.mkdir(parents=True, exist_ok=True)
            crop = crops_dir / f"{segment_id}.jpg"
            crop.write_bytes(p.face_crop)
        rows.append(LinePrint(segment_id=segment_id, job_id=job_id, voice_print=_blob(p.voice),
                              face_print=_blob(p.face), speaking_score=p.speaking_score,
                              face_crop_path=str(crop) if crop else None, model_version=model_version()))
    db.add_all(rows)
    await db.flush()


async def confirmed_labels(db: AsyncSession, job: Job, segments: list[Segment]) -> list[str | None]:
    """Per line, its character's label when the line is confirmed (a re-run keeps it), else None."""
    ids = list({s.speaker_id for s in segments if s.speaker_id})
    labels = dict((await db.execute(select(Speaker.id, Speaker.label).where(Speaker.id.in_(ids)))).all()) \
        if ids else {}
    return [labels.get(s.speaker_id) if is_confirmed(s, job) else None for s in segments]


def carried(spans: list[tuple[float, float]], confirmed: list[tuple[tuple[float, float], str]]) -> list[str | None]:
    """Per new span, the character whose confirmed old lines cover more than half of it (by overlap, as
    asr.labels_for_spans maps a fresh transcription), else None."""
    result = []
    for start, end in spans:
        cover: dict[str, float] = {}
        for (old_start, old_end), label in confirmed:
            seconds = min(end, old_end) - max(start, old_start)
            if seconds > 0:
                cover[label] = cover.get(label, 0.0) + seconds
        best = max(cover, key=cover.get, default=None)
        result.append(best if best is not None and cover[best] > (end - start) / 2 else None)
    return result


async def kept_characters(db: AsyncSession, job: Job, spans: list[tuple[float, float]]) -> list[str | None]:
    """A re-analysis replaces the episode's lines; per new span, the character it keeps from this episode's
    confirmed old lines (where their actor spoke), else None."""
    old = (await db.execute(select(Segment).where(Segment.job_id == job.id))).scalars().all()
    confirmed = [(actor_span(seg), label) for seg, label in zip(old, await confirmed_labels(db, job, old)) if label]
    return carried(spans, confirmed) if confirmed else [None] * len(spans)


async def recognise(
    db: AsyncSession, job: Job, spans: list[tuple[float, float]], gemini_labels: list[str],
    kept: list[str | None] | None = None,
) -> tuple[list[str], list[list[dict] | None], list[LinePrints]]:
    """Who speaks each line (spans: where the actor spoke). Per line: the label to use — `kept`'s when given (a
    confirmed character), else a known character's when the prints match one, else Gemini's — its who_check
    (None = sure; else the guesses, possibly none), and the prints ([] when they couldn't be made).

    Commits before the models run. Never fails: without vocals, or when the models can't run, Gemini's labels stand
    and nothing is marked to check, as before characters existed."""
    spans = list(spans)
    kept = list(kept) if kept else [None] * len(spans)
    labels = [k or label for k, label in zip(kept, gemini_labels)]
    unchecked: list[list[dict] | None] = [None] * len(spans)
    if not spans:
        return labels, unchecked, []
    vocals = Path(settings.UPLOAD_DIR) / job.project_id / job.id / "vocals.wav"
    if not vocals.exists():
        logger.warning(f"Job {job.id[:8]}: no vocals.wav, so characters weren't recognised — Gemini's speakers kept")
        return labels, unchecked, []

    profiles = await project_profiles(db, job.project_id, exclude_job_id=job.id)
    names = dict((await db.execute(select(Speaker.id, Speaker.label).where(Speaker.id.in_(list(profiles))))).all()) \
        if profiles else {}
    video = resolve_media_path(job.video_path)
    await db.commit()   # release the connection before minutes of models: the dev SQLite engine has one
    try:
        from app.services.characters import prints
        found = await asyncio.to_thread(prints.prints_for_spans, video, str(vocals), spans)
    except (ImportError, OSError, models.ModelMissing) as e:
        # e.g. a Mac below macOS 13.4 (onnxruntime), or a dev checkout without scripts/fetch-models.sh
        logger.warning(f"Job {job.id[:8]}: characters weren't recognised, the models can't run here: {e}")
        return labels, unchecked, []
    except Exception as e:
        logger.exception(f"Job {job.id[:8]}: recognising characters failed, Gemini's speakers kept: {e}")
        return labels, unchecked, []
    if len(found) != len(spans):
        logger.warning(f"Job {job.id[:8]}: {len(found)} prints for {len(spans)} lines — Gemini's speakers kept")
        return labels, unchecked, []
    if not profiles:   # the project's first episode: Gemini's speakers as before; its prints teach the next ones
        return labels, unchecked, found

    checks: list[list[dict] | None] = []
    for i, p in enumerate(found):
        if kept[i]:
            checks.append(None)
            continue
        decision = decide(p, profiles)
        if decision.speaker_id is not None:
            labels[i] = names[decision.speaker_id]
        checks.append(None if decision.sure else
                      [{"speaker_id": sid, "basis": basis} for sid, basis in decision.guesses])
    logger.info(f"Job {job.id[:8]}: characters recognised — {checks.count(None)} sure or kept, "
                f"{len(checks) - checks.count(None)} to check")
    return labels, checks, found
