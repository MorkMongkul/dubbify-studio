"""Characters in the database: which lines are confirmed, the profiles they make, the prints kept per line, and
recognising an episode's lines before translation.

A profile is recomputed from the stored prints of a character's confirmed lines every time, never kept as a running
sum, so a later correction takes effect at once. The models run only in `recognise`, imported there and with no
session open (SQLite has one writer), so a Mac that can't load them still analyses as before."""
import asyncio
import hashlib
import json
import logging
import platform
import sys
import threading
from collections import Counter
from contextlib import contextmanager, nullcontext, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from sqlalchemy import delete, event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.paths import resolve_media_path
from app.models.models import Job, LinePrint, Segment, Speaker
from app.services.characters import models, priority
from app.services.characters.decide import Profile, decide, overlapping, profile_from
from app.services.characters.types import LinePrints
from app.services.timeline import actor_span

logger = logging.getLogger(__name__)

VOICE_SIZE = 384   # characters.voiceprint.SIZE: ERes2NetV2's print, then CAM++'s (0.5.3; 192 before)
FACE_SIZE = 128
# One episode's models at a time: they share their sessions (lru_cache) and took 1.5 GB at peak in the spike
_ONE_AT_A_TIME = threading.Lock()
_COUNTING = threading.Lock()
_runs = 0   # runs waiting for the models or running them: the last one out releases them
_BACKGROUND: ContextVar[bool] = ContextVar("characters_background", default=False)   # at_background_priority
# Detect speakers while a project's checked lines all wait for new prints (the first start-up after new print models)
UPDATING_PRINTS = ("Movelo is still hearing this project's checked lines with its new voice models — a one-time job "
                   "after an update. Try Detect speakers again in a few minutes.")
# The models' cached loaders, by module
_LOADERS = (("voiceprint", "_extractor"), ("voiceprint", "_campplus"))
# The models can't run here: a Mac below macOS 13.4 (models.MIN_MACOS), or a dev checkout without scripts/fetch-models.sh
CANT_RUN = (ImportError, models.ModelMissing)
# How prints are made, beside the models themselves: bump it when that changes (1: 0.5.0), so every print is made again
# (0.5.3's CAM++ changed models.json, which does that by itself)
PRINTS_VERSION = 1

# Ruling AL: the jobs the start-up backfill will give today's prints (job id -> None, in its order). Only their
# confirmed lines wait — keep their project's lines from being sure. A confirmed line without a print anywhere else (a
# split's second half, a line added by hand) just doesn't teach until a later start-up's backfill gives it one. The
# lifespan fills it, awaited, before the backfill starts; the backfill drains it (a job leaves once its prints are
# saved or their making failed). Only the event loop touches it, and never across an await, so it needs no lock.
_backfill: dict[str, None] = {}
# Ruling AN: the projects whose characters the upgrade hasn't converted yet (project id -> the project's lines and
# characters as the start-up found them: upgrade moves only lines still as they were then). Every episode of theirs
# is queued above too, and every confirmed line of a queued episode waits while its project is here — its characters
# may still hold several actors. Filled and drained like the jobs.
_converting: dict[str, object] = {}


def queue_for_backfill(job_ids) -> None:
    for job_id in job_ids:
        _backfill.setdefault(job_id, None)


def backfill_pending() -> list[str]:
    return list(_backfill)


def backfill_done(job_id: str) -> None:
    _backfill.pop(job_id, None)


def queue_for_conversion(snapshots: dict[str, object]) -> None:
    for project_id, snapshot in snapshots.items():
        _converting.setdefault(project_id, snapshot)


def conversions_pending() -> list[str]:
    return list(_converting)


def converting(project_id: str) -> bool:
    return project_id in _converting


def conversion_snapshot(project_id: str) -> object | None:
    """The project's lines and characters as the start-up found them, or None when it isn't queued."""
    return _converting.get(project_id)


def conversion_done(project_id: str) -> None:
    _converting.pop(project_id, None)


def clear_backfill() -> None:
    _backfill.clear()
    _converting.clear()


@lru_cache(maxsize=1)
def model_version() -> str:
    """Which models made a print, and how (Ruling AM): the first 12 hex of the SHA-256 of models.json's sorted
    (name, sha256) pairs and PRINTS_VERSION. A model or a way of printing that changes makes new prints; a URL,
    licence, size or layout edit of models.json doesn't."""
    pairs = sorted((entry["name"], entry["sha256"]) for entry in json.loads(models.MANIFEST.read_text()))
    return hashlib.sha256(json.dumps([PRINTS_VERSION, pairs]).encode()).hexdigest()[:12]


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


@dataclass
class Known:
    """What a project's checked lines teach: each character's profile, how many confirmed lines have today's prints
    or wait, and how many wait — their job is queued for the start-up backfill, which hasn't reached it, and they
    have no print from today's models or their project's characters aren't converted yet. A project with none is at
    its first episode; while lines wait, the profiles are incomplete and no line may be sure."""
    profiles: dict[str, Profile]
    confirmed: int
    waiting: int

    @property
    def first_episode(self) -> bool:
        return self.confirmed == 0

    @property
    def complete(self) -> bool:
        return self.waiting == 0


async def known_characters(db: AsyncSession, project_id: str, exclude_job_id: str | None = None) -> Known:
    """The profiles from the stored prints of each character's confirmed lines (made by today's models; characters
    with no such print are left out), and the confirmed lines that wait in a job the start-up backfill queued: for
    those prints, or — every one of them — for the upgrade to convert their project's characters (Ruling AN). Any
    other confirmed line without a print neither waits nor counts."""
    query = (
        select(Segment, Job, LinePrint)
        .join(Job, Job.id == Segment.job_id)
        .join(Speaker, Speaker.id == Segment.speaker_id)
        .outerjoin(LinePrint, LinePrint.segment_id == Segment.id)
        .where(Job.project_id == project_id, Speaker.project_id == project_id)
        # the lines may have changed since this session last read them (a pick, the backfill's prints)
        .execution_options(populate_existing=True)
    )
    if exclude_job_id:
        query = query.where(Job.id != exclude_job_id)
    by_speaker: dict[str, list[LinePrints]] = {}
    counted = waiting = 0
    unconverted = converting(project_id)
    for seg, job, row in (await db.execute(query)).all():
        if not is_confirmed(seg, job):
            continue
        printed = row is not None and row.model_version == model_version()
        waits = job.id in _backfill and (unconverted or not printed)
        if not (printed or waits):
            continue
        counted += 1
        waiting += waits
        if printed:
            by_speaker.setdefault(seg.speaker_id, []).append(LinePrints(
                _vector(row.voice_print, VOICE_SIZE), _vector(row.face_print, FACE_SIZE), row.speaking_score, None))
    profiles = {speaker_id: profile_from(prints) for speaker_id, prints in by_speaker.items()}
    return Known({sid: p for sid, p in profiles.items() if p.voice is not None or p.face is not None},
                 counted, waiting)


async def project_profiles(db: AsyncSession, project_id: str, exclude_job_id: str | None = None) -> dict[str, Profile]:
    """{speaker id: profile} from the stored prints of each character's confirmed lines (see known_characters)."""
    return (await known_characters(db, project_id, exclude_job_id)).profiles


async def save_prints(db: AsyncSession, job_id: str, segment_ids: list[str], prints: list[LinePrints],
                      crops_dir: Path) -> None:
    """Keep each line's prints, replacing any it had, and its speaking face as crops_dir/<segment id>.jpg. Prints made
    without a face (voice only since 0.5.2) keep the face an older print of the line had — the characters' thumbnails
    (upgrade.refresh_faces) and the face half of "Same character?" come from them. Flushes; the caller commits (with
    the lines, so prints never outlive a failed swap)."""
    pairs = list(zip(segment_ids, prints))
    if not pairs:
        return
    ids = [sid for sid, _ in pairs]
    older = {row.segment_id: (row.face_print, row.speaking_score, row.face_crop_path) for row in (await db.execute(
        select(LinePrint).where(LinePrint.segment_id.in_(ids)))).scalars()}
    await db.execute(delete(LinePrint).where(LinePrint.segment_id.in_(ids)))
    rows, written = [], []
    for segment_id, p in pairs:
        crop = _write_crop(crops_dir, segment_id, p.face_crop) if p.face_crop else None
        if crop is not None:
            written.append(crop)
        face, score, crop_path = _blob(p.face), p.speaking_score, str(crop) if crop else None
        if face is None and crop_path is None and segment_id in older:
            face, score, crop_path = older[segment_id]
        rows.append(LinePrint(segment_id=segment_id, job_id=job_id, voice_print=_blob(p.voice), face_print=face,
                              speaking_score=score, face_crop_path=crop_path, model_version=model_version()))
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


@contextmanager
def at_background_priority():
    """The model runs started inside (from this task, through asyncio.to_thread, which carries the context) run at
    background priority: the start-up's older episodes, which nobody waits for. On an M1 that is the efficiency cores —
    2.6× as long as a normal run, at a fraction of the power, so a fanless Mac stays cool through the hour it takes."""
    token = _BACKGROUND.set(True)
    try:
        yield
    finally:
        _BACKGROUND.reset(token)


def _one_at_a_time(work, *args):
    """Runs in the worker thread: a second analysis waits here, never while holding a database session. The last run
    out releases the models (about 0.5 GB on an 8 GB Mac for the backend's life otherwise); the next run loads them
    again, in about a second. A background run loads its own and drops them after: a model's threads keep the
    priority they were started at."""
    global _runs
    background = _BACKGROUND.get()
    with _COUNTING:
        _runs += 1
    with _ONE_AT_A_TIME:
        if background:
            release_models()
        try:
            with priority.thread_at(priority.BACKGROUND) if background else nullcontext():
                return work(*args)
        finally:
            with _COUNTING:
                _runs -= 1
                last = _runs == 0
            if last or background:
                release_models()


def release_models() -> None:
    """Drop the models' loaded sessions. Only while holding _ONE_AT_A_TIME: no run is using them then. A module never
    imported has none."""
    for module_name, loader in _LOADERS:
        module = sys.modules.get(f"app.services.characters.{module_name}")
        if module is not None:
            getattr(module, loader).cache_clear()


def _prints_of(video: Path, vocals: str, spans: list[tuple[float, float]]) -> list[LinePrints]:
    """Worker thread, one analysis at a time: the lines' voice prints. Below macOS 13.4 it stops before anything imports
    the models' libraries (models.MIN_MACOS)."""
    if not models.runtime_supported():
        raise ImportError(f"character recognition needs macOS {'.'.join(map(str, models.MIN_MACOS))} or later "
                          f"(this Mac has {platform.mac_ver()[0]})")
    from app.services.characters import prints
    return prints.prints_for_spans(video, vocals, spans)


def vocals_path(job: Job) -> Path:
    """Where the job's separated vocals are (whether or not they are there)."""
    return Path(settings.UPLOAD_DIR) / job.project_id / job.id / "vocals.wav"


def _vocals_of(job: Job) -> Path | None:
    """The job's separated vocals, or None (logged): without them no print can be made."""
    vocals = vocals_path(job)
    if not vocals.exists():
        logger.warning(f"Job {job.id[:8]}: no vocals.wav, so characters weren't recognised — Gemini's speakers kept")
        return None
    return vocals


async def make_prints(db: AsyncSession, job: Job, spans: list[tuple[float, float]],
                      vocals: Path) -> list[LinePrints]:
    """The prints of `spans` (where the actor spoke), through the one-at-a-time lock. Commits first, so the models
    run with no connection held: the dev SQLite engine has one. Raises CANT_RUN when the models can't run here, and
    whatever else went wrong with this episode."""
    video = resolve_media_path(job.video_path)
    await db.commit()
    found = await asyncio.to_thread(_one_at_a_time, _prints_of, video, str(vocals), spans)
    if len(found) != len(spans):
        raise RuntimeError(f"{len(found)} prints for {len(spans)} lines")
    return found


async def _find_prints(db: AsyncSession, job: Job, spans: list[tuple[float, float]],
                       vocals: Path) -> list[LinePrints] | None:
    """make_prints, or None (logged) when the prints can't be made — the callers then keep Gemini's speakers."""
    try:
        return await make_prints(db, job, spans, vocals)
    except CANT_RUN as e:
        logger.warning(f"Job {job.id[:8]}: characters weren't recognised, the models can't run here: {e}",
                       exc_info=True)
        return None
    except Exception as e:   # OSError too: ffmpeg missing, too many open files, vocals.wav unreadable
        logger.exception(f"Job {job.id[:8]}: recognising characters failed, Gemini's speakers kept: {e}")
        return None


async def recognise(
    db: AsyncSession, job: Job, spans: list[tuple[float, float]], gemini_labels: list[str],
    kept: list[str | None] | None = None, refresh_kept=None,
) -> tuple[list[str], list[list[dict] | None], list[LinePrints]]:
    """Who speaks each line (spans: where the actor spoke). Per line: the label to use — `kept`'s when given (a
    confirmed character), else a known character's when the prints match one, else Gemini's — its who_check
    (None = sure; else the guesses, possibly none), and the prints ([] when they couldn't be made).

    The project's first episode (no confirmed line in its other episodes) keeps Gemini's labels, nothing to check.
    While confirmed lines wait for the start-up backfill's prints, no line is sure. The characters are read after
    the models ran, which may have meant waiting for another episode's (the backfill's).

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

    found = await _find_prints(db, job, spans, vocals)
    if refresh_kept is not None:   # a line confirmed while the models ran is a confirmed character's now
        kept = list(await refresh_kept())
        if given is not None:
            given[:] = kept
        labels = [k or label for k, label in zip(kept, gemini_labels)]
    if found is None:
        return labels, unchecked, []
    known = await known_characters(db, job.project_id, exclude_job_id=job.id)
    if known.first_episode:   # Gemini's speakers as before; its prints teach the next ones
        return labels, unchecked, found
    profiles = known.profiles
    names = dict((await db.execute(select(Speaker.id, Speaker.label).where(Speaker.id.in_(list(profiles))))).all()) \
        if profiles else {}

    checks: list[list[dict] | None] = []
    mixed = overlapping(spans)
    for i, p in enumerate(found):
        if kept[i]:
            checks.append(None)
            continue
        decision = decide(p, profiles, mixed_voice=mixed[i], may_be_sure=known.complete)
        if decision.speaker_id is not None:
            labels[i] = names[decision.speaker_id]
        checks.append(None if decision.sure else
                      [{"speaker_id": sid, "basis": basis} for sid, basis in decision.guesses])
    logger.info(f"Job {job.id[:8]}: characters recognised — {checks.count(None)} sure or kept, "
                f"{len(checks) - checks.count(None)} to check"
                + ("" if known.complete else f" ({known.waiting} checked line(s) still wait for their prints or "
                                             f"the upgrade)"))
    return labels, checks, found


async def identify(db: AsyncSession, job: Job, segments: list[Segment]) -> str:
    """Detect speakers without Gemini: every line that isn't confirmed is checked against the project's characters.
    A known character takes the line (who_check None when sure, else its guesses); a voice nobody has leaves the line
    on its speaker, marked to check. Text, timing and confirmed lines are never touched. Prints are made only for
    lines without today's, in one run of the models, and kept.

    While confirmed lines in other episodes wait for the start-up backfill's prints, no line is sure. The
    characters are read again after the models ran.

    Returns "done", or — nothing changed, the caller falls back to Gemini — why it couldn't run: "no_profiles" (the
    project has no characters yet), "no_vocals", or "cant_run" (the models can't run; logged). "waiting": no profile
    yet only because every checked line waits for the backfill's prints — nothing changed, and the caller must not
    fall back to Gemini (its fresh labels would move every open line). Commits before the models run; the lines are
    read again afterwards (the user may have edited or deleted some). Flushes; the caller commits."""
    known = await known_characters(db, job.project_id, exclude_job_id=job.id)
    if not known.profiles:
        return "waiting" if known.waiting else "no_profiles"
    open_ids = [s.id for s in segments if not is_confirmed(s, job)]
    if not open_ids:
        return "done"
    # a line spoken over another of the episode (confirmed or not) has a mixed voice print
    mixed = dict(zip([s.id for s in segments], overlapping([actor_span(s) for s in segments])))
    vocals = _vocals_of(job)
    if vocals is None:
        return "no_vocals"
    stored = {row.segment_id: row for row in
              (await db.execute(select(LinePrint).where(LinePrint.segment_id.in_(open_ids)))).scalars()}
    todo = [s for s in segments if s.id in open_ids
            and (s.id not in stored or stored[s.id].model_version != model_version())]
    computed: dict[str, LinePrints] = {}
    if todo:
        found = await _find_prints(db, job, [actor_span(s) for s in todo], vocals)
        if found is None:
            return "cant_run"
        computed = {s.id: p for s, p in zip(todo, found)}

    # the user may have deleted, edited or confirmed lines while the models ran
    # populate_existing: with expire_on_commit off the lines are still in the session as they were before
    current = {s.id: s for s in (await db.execute(
        select(Segment).where(Segment.id.in_(open_ids)).execution_options(populate_existing=True))).scalars()}
    await db.refresh(job)
    still_open = [sid for sid in open_ids if sid in current and not is_confirmed(current[sid], job)]
    await save_prints(db, job.id, [sid for sid in computed if sid in current],
                      [computed[sid] for sid in computed if sid in current], faces_dir(job))
    known = await known_characters(db, job.project_id, exclude_job_id=job.id)   # after the models: complete now?
    profiles = known.profiles
    for sid in still_open:
        row = stored.get(sid)
        p = computed.get(sid) or LinePrints(_vector(row.voice_print, VOICE_SIZE), _vector(row.face_print, FACE_SIZE),
                                            row.speaking_score, None)
        decision = decide(p, profiles, mixed_voice=mixed[sid], may_be_sure=known.complete)
        guesses = [{"speaker_id": g, "basis": basis} for g, basis in decision.guesses]
        if decision.speaker_id is not None:
            current[sid].speaker_id = decision.speaker_id
        current[sid].who_check = who_check_json(None if decision.sure else guesses)
    await db.flush()
    logger.info(f"Job {job.id[:8]}: {len(still_open)} lines checked against {len(profiles)} characters, "
                f"{len(computed)} prints made")
    return "done"


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
