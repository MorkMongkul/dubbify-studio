"""Characters in the database: which lines are confirmed, the profiles they make, the prints kept per line, and
recognising an episode's lines before translation.

A profile is recomputed from the stored prints of a character's confirmed lines every time, never kept as a running
sum, so a later correction takes effect at once. The models run only in `recognise`, imported there and with no
session open (SQLite has one writer), so a Mac that can't load them still analyses as before."""
import asyncio
import hashlib
import json
import logging
import threading
from collections import Counter
from contextlib import suppress
from functools import lru_cache
from pathlib import Path

import numpy as np
from sqlalchemy import delete, event, select
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
# One episode's models at a time: they share their sessions (lru_cache) and took 1.5 GB at peak in the spike
_ONE_AT_A_TIME = threading.Lock()


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


def _write_crop(crops_dir: Path, segment_id: str, jpeg: bytes) -> Path | None:
    """crops_dir/<segment id>.jpg, or None (logged) when it can't be written: the print is kept without it."""
    crop = crops_dir / f"{segment_id}.jpg"
    try:
        crops_dir.mkdir(parents=True, exist_ok=True)
        crop.write_bytes(jpeg)
    except OSError as e:
        logger.warning(f"Line {segment_id[:8]}: its face crop couldn't be saved, the print is kept without: {e}")
        with suppress(OSError):
            crop.unlink(missing_ok=True)
        return None
    return crop


def _removed_on_rollback(db: AsyncSession, files: list[Path]) -> None:
    """The crops are written before the rows naming them are committed; if that transaction rolls back instead,
    they go too, so a failed analysis leaves no files behind."""
    if not files:
        return
    pending = list(files)

    def committed(_session) -> None:
        pending.clear()

    def rolled_back(_session) -> None:
        for file in pending:
            with suppress(OSError):
                file.unlink(missing_ok=True)
        pending.clear()

    # once: whichever comes first settles them, and the other then finds nothing to do
    event.listen(db.sync_session, "after_commit", committed, once=True)
    event.listen(db.sync_session, "after_rollback", rolled_back, once=True)


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
    rows, written = [], []
    for segment_id, p in pairs:
        crop = _write_crop(crops_dir, segment_id, p.face_crop) if p.face_crop else None
        if crop is not None:
            written.append(crop)
        rows.append(LinePrint(segment_id=segment_id, job_id=job_id, voice_print=_blob(p.voice),
                              face_print=_blob(p.face), speaking_score=p.speaking_score,
                              face_crop_path=str(crop) if crop else None, model_version=model_version()))
    _removed_on_rollback(db, written)
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
    # populate_existing: the lines may have changed since the session last read them (analysis commits, then waits)
    old = (await db.execute(
        select(Segment).where(Segment.job_id == job.id).execution_options(populate_existing=True))).scalars().all()
    await db.refresh(job)
    confirmed = [(actor_span(seg), label) for seg, label in zip(old, await confirmed_labels(db, job, old)) if label]
    return carried(spans, confirmed) if confirmed else [None] * len(spans)


def _one_at_a_time(work, *args):
    """Runs in the worker thread: a second analysis waits here, never while holding a database session."""
    with _ONE_AT_A_TIME:
        return work(*args)


def _vocals_of(job: Job) -> Path | None:
    """The job's separated vocals, or None (logged): without them no print can be made."""
    vocals = Path(settings.UPLOAD_DIR) / job.project_id / job.id / "vocals.wav"
    if not vocals.exists():
        logger.warning(f"Job {job.id[:8]}: no vocals.wav, so characters weren't recognised — Gemini's speakers kept")
        return None
    return vocals


async def _find_prints(db: AsyncSession, job: Job, spans: list[tuple[float, float]],
                       vocals: Path) -> list[LinePrints] | None:
    """The prints of `spans` (where the actor spoke), or None (logged) when they can't be made — the callers then
    keep Gemini's speakers. Commits first, so the models run with no connection held: the dev SQLite engine has one."""
    video = resolve_media_path(job.video_path)
    await db.commit()
    try:
        from app.services.characters import prints
        found = await asyncio.to_thread(_one_at_a_time, prints.prints_for_spans, video, str(vocals), spans)
    except (ImportError, models.ModelMissing) as e:
        # a Mac below macOS 13.4 (onnxruntime), or a dev checkout without scripts/fetch-models.sh
        logger.warning(f"Job {job.id[:8]}: characters weren't recognised, the models can't run here: {e}",
                       exc_info=True)
        return None
    except Exception as e:   # OSError too: ffmpeg missing, too many open files, vocals.wav unreadable
        logger.exception(f"Job {job.id[:8]}: recognising characters failed, Gemini's speakers kept: {e}")
        return None
    if len(found) != len(spans):
        logger.warning(f"Job {job.id[:8]}: {len(found)} prints for {len(spans)} lines — Gemini's speakers kept")
        return None
    return found


async def recognise(
    db: AsyncSession, job: Job, spans: list[tuple[float, float]], gemini_labels: list[str],
    kept: list[str | None] | None = None, refresh_kept=None,
) -> tuple[list[str], list[list[dict] | None], list[LinePrints]]:
    """Who speaks each line (spans: where the actor spoke). Per line: the label to use — `kept`'s when given (a
    confirmed character), else a known character's when the prints match one, else Gemini's — its who_check
    (None = sure; else the guesses, possibly none), and the prints ([] when they couldn't be made).

    Commits before the models run. Never fails: without vocals, or when the models can't run, Gemini's labels stand
    and nothing is marked to check, as before characters existed.

    `kept` is read before the models' minutes, so a line confirmed meanwhile would be missed: `refresh_kept`, an async
    function returning it again, is called after they ran, and `kept` is updated in place."""
    spans = list(spans)
    given = kept
    kept = list(kept) if kept else [None] * len(spans)
    labels = [k or label for k, label in zip(kept, gemini_labels)]
    unchecked: list[list[dict] | None] = [None] * len(spans)
    if not spans:
        return labels, unchecked, []
    vocals = _vocals_of(job)
    if vocals is None:
        return labels, unchecked, []

    profiles = await project_profiles(db, job.project_id, exclude_job_id=job.id)
    names = dict((await db.execute(select(Speaker.id, Speaker.label).where(Speaker.id.in_(list(profiles))))).all()) \
        if profiles else {}
    found = await _find_prints(db, job, spans, vocals)
    if refresh_kept is not None:   # a line confirmed while the models ran is a confirmed character's now
        kept = list(await refresh_kept())
        if given is not None:
            given[:] = kept
        labels = [k or label for k, label in zip(kept, gemini_labels)]
    if found is None:
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


async def identify(db: AsyncSession, job: Job, segments: list[Segment]) -> bool:
    """Detect speakers without Gemini: every line that isn't confirmed is checked against the project's characters.
    A known character takes the line (who_check None when sure, else its guesses); a voice nobody has leaves the line
    on its speaker, marked to check. Text, timing and confirmed lines are never touched. Prints are made only for
    lines without today's, in one run of the models, and kept.

    Returns False — nothing changed — when it can't run: the project has no characters yet, there are no vocals, or
    the models can't run (logged), and the caller falls back to Gemini. Commits before the models run; the lines
    are read again afterwards (the user may have edited or deleted some). Flushes; the caller commits."""
    profiles = await project_profiles(db, job.project_id, exclude_job_id=job.id)
    if not profiles:
        return False
    open_ids = [s.id for s in segments if not is_confirmed(s, job)]
    if not open_ids:
        return True
    vocals = _vocals_of(job)
    if vocals is None:
        return False
    stored = {row.segment_id: row for row in
              (await db.execute(select(LinePrint).where(LinePrint.segment_id.in_(open_ids)))).scalars()}
    todo = [s for s in segments if s.id in open_ids
            and (s.id not in stored or stored[s.id].model_version != model_version())]
    computed: dict[str, LinePrints] = {}
    if todo:
        found = await _find_prints(db, job, [actor_span(s) for s in todo], vocals)
        if found is None:
            return False
        computed = {s.id: p for s, p in zip(todo, found)}

    # the user may have deleted, edited or confirmed lines while the models ran
    # populate_existing: with expire_on_commit off the lines are still in the session as they were before
    current = {s.id: s for s in (await db.execute(
        select(Segment).where(Segment.id.in_(open_ids)).execution_options(populate_existing=True))).scalars()}
    await db.refresh(job)
    still_open = [sid for sid in open_ids if sid in current and not is_confirmed(current[sid], job)]
    await save_prints(db, job.id, [sid for sid in computed if sid in current],
                      [computed[sid] for sid in computed if sid in current], faces_dir(job))
    alive = set((await db.execute(select(Speaker.id).where(Speaker.id.in_(list(profiles))))).scalars())
    profiles = {sid: p for sid, p in profiles.items() if sid in alive}
    for sid in still_open:
        row = stored.get(sid)
        p = computed.get(sid) or LinePrints(_vector(row.voice_print, VOICE_SIZE), _vector(row.face_print, FACE_SIZE),
                                            row.speaking_score, None)
        decision = decide(p, profiles)
        guesses = [{"speaker_id": g, "basis": basis} for g, basis in decision.guesses]
        if decision.speaker_id is not None:
            current[sid].speaker_id = decision.speaker_id
        current[sid].who_check = who_check_json(None if decision.sure else guesses)
    await db.flush()
    logger.info(f"Job {job.id[:8]}: {len(still_open)} lines checked against {len(profiles)} characters, "
                f"{len(computed)} prints made")
    return True


def heard_by_label(labels: list[str], gemini_labels: list[str], who_checks: list[list[dict] | None],
                   kept: list[str | None], voices: list[str | None]) -> dict[str, str]:
    """The voice Gemini heard (man/woman/boy/girl) per label, by majority of the lines whose label can be trusted:
    kept, sure, or still Gemini's own. A line moved to a known character but still to check doesn't count: misheard
    or misrecognised, it would set that character's gender and flip បាទ/ចាស across the whole episode."""
    votes: dict[str, Counter] = {}
    # backwards, so a tie goes to the later line, as when the last line heard simply won
    for label, gemini, check, keep, voice in reversed(list(zip(labels, gemini_labels, who_checks, kept, voices))):
        if voice and (keep or check is None or label == gemini):
            votes.setdefault(label, Counter())[voice] += 1
    return {label: count.most_common(1)[0][0] for label, count in votes.items()}
