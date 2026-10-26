"""Existing projects meet characters (0.5.0).

Before characters, labels came only from what Gemini found in each episode: when an actor had no label, the owner
re-used one and changed its voice, or gave a single line its own voice. So a label's voice today doesn't say who spoke
an older line; the voice its Khmer audio was made with does — one voice is one actor for the whole series.
`convert_project` puts each line of a project from before (`projects.characters_version` below CHARACTERS_VERSION) on
the character of that voice, once: the line's own voice when it has one, else the voice its audio clearly sounds like
(Ruling AN). Hearing the audio needs the voice model, so it runs in the start-up's background, per project, before
that project's prints; until it has, the project's episodes stay queued and none of its lines is sure. The rest runs
at every start-up and can resume: the prints of lines that have none (or stale ones), the sweep of face crops no print
names, and each character's thumbnail. Nothing here fails the start-up."""
import asyncio
import logging
import os
import platform
import re
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.paths import resolve_media_path
from app.models.models import (
    CHARACTERS_VERSION, AgeGroup, Gender, Job, LinePrint, Project, Segment, Speaker, Voice,
)
from app.services.characters import models, store
from app.services.diarizer import build_voice_design_prompt
from app.services.timeline import actor_span

logger = logging.getLogger(__name__)

__all__ = ["CHARACTERS_VERSION", "VOICE_MARGIN", "convert_project", "finish_upgrade", "queue_backfill"]

# A line's audio names the voice it was made with only when it sounds like that voice's reference clip by at least
# this much more than like any other (cosine of ERes2NetV2 prints). Measured on the owner's project: right on 15 of
# 15 lines with a hand-set voice; the median lead was 0.128, and 22 of 301 lines led by less than this.
VOICE_MARGIN = 0.05


def _next_label(taken: set[str]) -> str:
    """The next free SPEAKER_XX: after the highest number ever used here, so a label a merge removed isn't reused
    (the Story notes may still link a character to it)."""
    numbers = [int(m.group(1)) for label in taken if (m := re.fullmatch(r"SPEAKER_(\d+)", label))]
    n = max(numbers, default=-1) + 1
    while f"SPEAKER_{n:02d}" in taken:
        n += 1
    return f"SPEAKER_{n:02d}"


def _below_version():
    return or_(Project.characters_version.is_(None), Project.characters_version < CHARACTERS_VERSION)


def _converted(project: Project | None) -> bool:
    """Gone, or already brought up to today's rules."""
    return project is None or (project.characters_version or 0) >= CHARACTERS_VERSION


@dataclass(frozen=True)
class _Line:
    """A line of a project to convert, as the start-up found it. `heard` is the file its voice is heard from — the
    engine's voice before the line's effects (seg_<id>_raw.wav) when it's there, else the clip — for a voiced line
    without a voice of its own; `made` is when that file was written."""
    speaker_id: str | None
    voice_id: str | None
    confirmed: bool
    stored: str | None
    heard: str | None = None
    made: int | None = None


@dataclass(frozen=True)
class _Snapshot:
    """A project's lines and its characters' voices as the start-up found them. The conversion moves only a line
    still exactly as it was then, whose character still has the voice it had: anything the user did in 0.5.0 before
    the project's turn came (its episodes' prints come one project after another), or while its audio was heard,
    stands — a character picked, a voice of its own (casting only since 0.5.0), a voice made again, a re-cast. A line
    added since is never moved."""
    lines: dict[str, _Line]
    voices: dict[str, str | None]


def _made_at(path: str) -> int | None:
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


def _file_to_hear(segment_id: str, stored: str | None) -> str | None:
    """The engine's own voice (the raw clip every effect, speed and level is rendered from: a filter can fool a voice
    print), else the line's clip; None when neither is there."""
    if not stored:
        return None
    clip = Path(resolve_media_path(stored))
    return next((str(f) for f in (clip.with_name(f"seg_{segment_id}_raw.wav"), clip) if f.is_file()), None)


async def _snapshot(db: AsyncSession, project_id: str) -> _Snapshot:
    """The project's lines and characters as they are now (a few file checks per voiced line)."""
    voices = dict((await db.execute(
        select(Speaker.id, Speaker.voice_id).where(Speaker.project_id == project_id))).all())
    lines = {}
    for segment_id, speaker_id, voice_id, confirmed, stored in (await db.execute(
        select(Segment.id, Segment.speaker_id, Segment.voice_id, Segment.who_confirmed, Segment.tts_audio_path)
        .join(Job, Job.id == Segment.job_id).where(Job.project_id == project_id)
    )).all():
        heard = None if voice_id else _file_to_hear(segment_id, stored)
        lines[segment_id] = _Line(speaker_id, voice_id, bool(confirmed), stored, heard,
                                  _made_at(heard) if heard else None)
    return _Snapshot(lines, voices)


def _as_at_start_up(seg: Segment, at: _Line | None, voices_now: dict, snapshot: _Snapshot) -> bool:
    return (at is not None
            and (seg.speaker_id, seg.voice_id, bool(seg.who_confirmed), seg.tts_audio_path)
            == (at.speaker_id, at.voice_id, at.confirmed, at.stored)
            and voices_now.get(seg.speaker_id) == snapshot.voices.get(seg.speaker_id)
            and (at.heard is None or _made_at(at.heard) == at.made))


async def _move_lines(db: AsyncSession, project_id: str, heard: dict[str, str], snapshot: _Snapshot) -> list[Segment]:
    """Every line still as `snapshot` found it whose voice then — its own when it had one, else the one `heard` says
    its audio was made with — differs from its character's (or that has no character) moves to the character a
    person cast with that voice: the one with the most lines when several, the lowest label on a tie — or to a new
    character for a voice no person gave anyone (one per voice, no name). A voice "Choose for me" picked (voice_auto)
    says nothing about who the actor is: it repeats on unrelated characters. The line's own voice is cleared (its
    character carries it now) and the line counts as confirmed. Text, timing and audio are untouched; a label left
    with no line stays. Reads the lines and characters again; flushes, the caller commits. Returns the lines moved."""
    speakers = (await db.execute(select(Speaker).where(Speaker.project_id == project_id)
                                 .execution_options(populate_existing=True))).scalars().all()
    voices_now = {sp.id: sp.voice_id for sp in speakers}
    lines = (await db.execute(
        select(Segment).join(Job, Job.id == Segment.job_id).where(Job.project_id == project_id)
        .execution_options(populate_existing=True)
    )).scalars().all()
    made_with = {}
    for s in lines:
        at = snapshot.lines.get(s.id)
        if _as_at_start_up(s, at, voices_now, snapshot):
            made_with[s.id] = at.voice_id or heard.get(s.id)
    moves = [s for s in lines if made_with.get(s.id) and made_with[s.id] != voices_now.get(s.speaker_id)]
    if not moves:
        return []
    counts = Counter(s.speaker_id for s in lines)
    taken = {sp.label for sp in speakers}
    target_of: dict[str, Speaker] = {}
    for voice_id in sorted({made_with[s.id] for s in moves}):
        cast = [sp for sp in speakers if sp.voice_id == voice_id and not sp.voice_auto]
        if cast:
            target_of[voice_id] = min(cast, key=lambda sp: (-counts[sp.id], sp.label))
            continue
        label = _next_label(taken)
        taken.add(label)
        target = Speaker(project_id=project_id, label=label, display_name="", gender=Gender.UNKNOWN,
                         age_group=AgeGroup.ADULT, voice_design_prompt=build_voice_design_prompt(label, "unknown", "adult"),
                         voice_id=voice_id, voice_auto=False)
        db.add(target)
        target_of[voice_id] = target
    await db.flush()
    for line in moves:
        line.speaker_id = target_of[made_with[line.id]].id
        line.voice_id = None
        line.who_confirmed, line.who_check = True, None
    await db.flush()
    return moves


async def convert_voice_overrides(db: AsyncSession, project_id: str, heard: dict[str, str] | None = None) -> int:
    """_move_lines with the project as it is now: `heard` {segment id: voice id} names the voice the audio of lines
    without their own was made with (none: only lines with their own voice move, the rule of version 1). Returns how
    many lines moved."""
    return len(await _move_lines(db, project_id, heard or {}, await _snapshot(db, project_id)))


async def _reference_clips(db: AsyncSession) -> dict[str, str]:
    """{voice id: its reference clip} for every voice whose clip is there — built-in and the user's own, used in
    this project or not (the actor of a character who died in episode 1 has no character's voice today)."""
    clips = {}
    for voice_id, stored in (await db.execute(
        select(Voice.id, Voice.reference_audio_path).where(Voice.reference_audio_path.is_not(None),
                                                           Voice.reference_audio_path != "").order_by(Voice.id)
    )).all():
        path = resolve_media_path(stored)
        if Path(path).is_file():
            clips[voice_id] = path
    return clips


def _voices_made_with(reference_clips: dict[str, str], line_audio: dict[str, str],
                      reference_prints: dict) -> dict[str, str]:
    """Worker thread, through store's one-at-a-time lock: {segment id: the voice its audio was made with}, for the
    lines whose audio (`line_audio`, {segment id: file}) sounds like one voice's reference clip (`reference_clips`,
    {voice id: file}) by at least VOICE_MARGIN more than like any other — ERes2NetV2 prints of the whole files,
    16 kHz mono, as the line prints are made. `reference_prints` keeps the clips' prints for the start-up's run
    ({(file, modified): print}). A line or reference clip that can't be read is left out (logged once); raises
    CANT_RUN when the models can't run here (below macOS 13.4 before anything imports them), and the first error when
    no reference clip could be read."""
    if not models.runtime_supported():
        raise ImportError(f"character recognition needs macOS {'.'.join(map(str, models.MIN_MACOS))} or later "
                          f"(this Mac has {platform.mac_ver()[0]})")
    from app.services.characters import voiceprint

    prints, failed = {}, []
    for voice_id, path in reference_clips.items():
        key = (path, _made_at(path))
        if key not in reference_prints:
            try:
                reference_prints[key] = voiceprint.file_print(path)
            except store.CANT_RUN:
                raise
            except Exception as e:  # noqa: BLE001 — that voice is left out
                failed.append(e)
                continue
        if reference_prints[key] is not None:
            prints[voice_id] = reference_prints[key]
    if failed and not prints:
        raise failed[0]
    if len(prints) < 2:
        raise RuntimeError(f"{len(prints)} voice(s)' reference clips could be heard, too few to tell voices apart")
    if failed:
        logger.warning(f"{len(failed)} voice(s)' reference clips couldn't be heard, so no line goes to them: "
                       f"{failed[0]}")
    ids = list(prints)
    voices = np.stack([prints[voice_id] for voice_id in ids])
    found, unreadable = {}, []
    for segment_id, path in line_audio.items():
        try:
            heard = voiceprint.file_print(path)
        except store.CANT_RUN:
            raise
        except Exception as e:  # noqa: BLE001 — that line stays where it is
            unreadable.append(e)
            continue
        if heard is None:
            continue
        cosines = voices @ heard
        second, best = np.argsort(cosines)[-2:]
        if cosines[best] - cosines[second] >= VOICE_MARGIN:
            found[segment_id] = ids[best]
    if unreadable:
        logger.warning(f"{len(unreadable)} line(s)' Khmer voices couldn't be heard, they stay where they are: "
                       f"{unreadable[0]}")
    return found


def _cannot_hear(project_id: str, why) -> None:
    logger.warning(f"Project {project_id[:8]}: the voices its lines were made with couldn't be heard ({why}), so only "
                   f"lines with their own voice moved to characters")


async def convert_project(sessionmaker, project_id: str, with_models: bool = True,
                          reference_prints: dict | None = None) -> int:
    """Puts each line of a project below CHARACTERS_VERSION on the character of the voice it was made with
    (_move_lines): its own voice, else the voice its audio was clearly made with — heard with the voice model, in a
    thread through store's one-at-a-time lock, with no connection held — and marks the project done, in one commit.
    Only lines still as the start-up found them move (store keeps that snapshot; without one, e.g. from a script, the
    project as it is now). When the voices can't be heard (`with_models` False: the models can't run here; fewer than
    two voices with a reference clip; or the model fails) only lines with their own voice move, the project is still
    marked done (never retried at every start-up), and one line is logged. `reference_prints` keeps the voices'
    reference clip prints for the start-up's run. Never raises (an error leaves the project for the next start-up,
    logged); the project leaves store's conversion queue either way. Returns how many lines moved."""
    reference_prints = {} if reference_prints is None else reference_prints
    snapshot = store.conversion_snapshot(project_id)
    try:
        async with sessionmaker() as db:
            if _converted(await db.get(Project, project_id)):
                return 0
            if snapshot is None:
                snapshot = await _snapshot(db, project_id)
            to_hear = {sid: line.heard for sid, line in snapshot.lines.items() if line.heard}
            reference_clips = await _reference_clips(db) if to_hear else {}
            await db.commit()   # nothing held while the voices are heard (the dev SQLite engine has one connection)
            heard: dict[str, str] = {}
            if to_hear and not with_models:
                _cannot_hear(project_id, "the models can't run here")
            elif to_hear and len(reference_clips) < 2:
                _cannot_hear(project_id, f"{len(reference_clips)} voice(s) have a reference clip, too few to tell "
                                         f"voices apart")
            elif to_hear:
                try:
                    heard = await asyncio.to_thread(store._one_at_a_time, _voices_made_with, reference_clips,
                                                    to_hear, reference_prints)
                except Exception as e:  # noqa: BLE001 — CANT_RUN, or the model failed: degrade to version 1's rule
                    _cannot_hear(project_id, e)
            # the user may have picked, re-voiced, re-cast, edited or deleted while the audio was heard
            project = await db.get(Project, project_id, populate_existing=True)
            if _converted(project):
                return 0
            moved = await _move_lines(db, project_id, heard, snapshot)
            project.characters_version = CHARACTERS_VERSION
            await db.commit()
    except Exception:  # noqa: BLE001 — not fatal: the project stays as it was and the next start-up tries again
        logger.exception(f"Couldn't move project {project_id[:8]}'s lines to the characters of their voices")
        return 0
    finally:
        store.conversion_done(project_id)
    by_ear = sum(1 for s in moved if s.id in heard)
    if moved or to_hear:
        logger.info(f"Project {project_id[:8]}: {len(moved)} line(s) moved to the character of the voice they were "
                    f"made with — {len(moved) - by_ear} by their own voice, {by_ear} by ear ({len(to_hear)} voiced "
                    f"line(s) heard, {len(heard)} clearly)")
    return len(moved)


def _crop_names(paths) -> set[str]:
    return {Path(p).name for p in paths if p}


def _sweep_folder(folder: Path, keep: set[str]) -> int:
    removed = 0
    for file in folder.iterdir():
        if file.is_file() and file.name not in keep:
            with suppress(OSError):
                file.unlink()
                removed += 1
    return removed


async def sweep_orphan_crops(sessionmaker) -> int:
    """Re-analysis, split and delete leave face crops behind (files are never removed inside a request's
    transaction): remove the files in each job's faces/ folder that no line print names. Returns how many."""
    removed = 0
    async with sessionmaker() as db:
        jobs = (await db.execute(select(Job.id, Job.project_id))).all()
        named = {}
        for job_id, paths in (await db.execute(select(LinePrint.job_id, LinePrint.face_crop_path))).all():
            named.setdefault(job_id, set()).update(_crop_names([paths]))
    for job_id, project_id in jobs:
        folder = Path(settings.UPLOAD_DIR) / project_id / job_id / "faces"
        if folder.is_dir():
            removed += await asyncio.to_thread(_sweep_folder, folder, named.get(job_id, set()))
    if removed:
        logger.info(f"Removed {removed} face crop(s) no line uses any more")
    return removed


async def refresh_faces(db: AsyncSession, project_id: str) -> None:
    """Each character's thumbnail becomes the best-scored existing crop among its confirmed lines, or None. One
    query for the project, no models. Commits when a thumbnail changed."""
    rows = (await db.execute(
        select(Segment, Job, LinePrint).join(Job, Job.id == Segment.job_id)
        .join(LinePrint, LinePrint.segment_id == Segment.id)
        .where(Job.project_id == project_id, LinePrint.face_crop_path.is_not(None), Segment.speaker_id.is_not(None))
        .execution_options(populate_existing=True)
    )).all()
    best: dict[str, tuple[float, str]] = {}
    for seg, job, row in rows:
        if not store.is_confirmed(seg, job):
            continue
        path = resolve_media_path(row.face_crop_path)
        score = row.speaking_score if row.speaking_score is not None else -1.0
        if (seg.speaker_id not in best or score > best[seg.speaker_id][0]) and Path(path).is_file():
            best[seg.speaker_id] = (score, row.face_crop_path)
    changed = False
    for speaker in (await db.execute(select(Speaker).where(Speaker.project_id == project_id))).scalars():
        wanted = best[speaker.id][1] if speaker.id in best else None
        if speaker.face_crop_path != wanted:
            speaker.face_crop_path = wanted
            changed = True
    if changed:
        await db.commit()


async def refresh_faces_safely(db: AsyncSession, project_id: str) -> None:
    """refresh_faces for the callers whose real work is already committed: a thumbnail that can't be refreshed
    (logged) must not fail an analysis or a pick."""
    try:
        await refresh_faces(db, project_id)
    except Exception:  # noqa: BLE001
        logger.exception(f"Couldn't refresh project {project_id[:8]}'s character thumbnails")
        with suppress(Exception):
            await db.rollback()


async def _jobs_to_queue(db: AsyncSession, converting: list[str]) -> list[str]:
    """Jobs with a line that has no print from today's models, and every job of a project whose characters the
    upgrade will convert: the project worked on most recently first (by its newest episode; until its profiles are
    complete, no line of its next episode can be sure), each project's newest episodes first."""
    newest = (select(Job.project_id, func.max(Job.created_at).label("newest"))
              .group_by(Job.project_id).subquery())
    waiting = (select(Segment.job_id).outerjoin(LinePrint, LinePrint.segment_id == Segment.id)
               .where((LinePrint.segment_id.is_(None)) | (LinePrint.model_version != store.model_version())))
    return (await db.execute(
        select(Job.id).join(newest, newest.c.project_id == Job.project_id)
        .where(Job.id.in_(waiting) | Job.project_id.in_(converting))
        .order_by(newest.c.newest.desc(), Job.project_id, Job.created_at.desc(), Job.id)
    )).scalars().all()


async def queue_backfill(sessionmaker) -> int:
    """What the start-up's background will do, queued in store before it starts (the lifespan awaits this, so no
    request can come in first): the projects whose characters it will convert (Ruling AN), each with its lines and
    characters as they are now (the conversion moves only lines still so) and every one of its episodes, and the
    jobs it will give prints — those with separated vocals (the others never get prints) — so the
    confirmed lines of those episodes wait: no line of their projects is sure before they have their prints and
    their project's characters are converted (Ruling AL). Quick database work. Returns how many jobs were queued."""
    async with sessionmaker() as db:
        converting = (await db.execute(select(Project.id).where(_below_version()).order_by(Project.id))).scalars().all()
        # what the conversion may move: the lines as they are before the user can touch anything
        snapshots = {project_id: await _snapshot(db, project_id) for project_id in converting}
        ids = await _jobs_to_queue(db, converting)
        jobs = {job.id: job for job in (await db.execute(select(Job).where(Job.id.in_(ids)))).scalars()}
    queued = [job_id for job_id in ids
              if jobs[job_id].project_id in converting or store.vocals_path(jobs[job_id]).exists()]
    store.queue_for_conversion(snapshots)
    store.queue_for_backfill(queued)
    return len(queued)


def _next_queued(tried: set[str]) -> str | None:
    return next((job_id for job_id in store.backfill_pending() if job_id not in tried), None)


async def backfill_prints(sessionmaker, reference_prints: dict | None = None) -> bool:
    """The queued jobs one by one: first, at a project's first job, its characters' conversion when it waits for
    one (convert_project); then prints for the job's lines with none (or made by other models), through the
    one-at-a-time lock, queuing everything first when nothing is queued. The models run with no connection held
    (`store.make_prints` commits first). A job leaves the queue once its prints are saved or their making failed: a
    failing episode is logged and skipped, so it never keeps the others from theirs nor holds its project back.
    Returns False when the models can't run here (logged once): no more prints are tried, and the projects still to
    convert get version 1's rule only."""
    if not store.backfill_pending() and not store.conversions_pending():
        await queue_backfill(sessionmaker)
    reference_prints = {} if reference_prints is None else reference_prints
    models_run = True
    tried: set[str] = set()
    # until the queue is empty: a job queued while this runs gets its turn too
    while (job_id := _next_queued(tried)) is not None:
        tried.add(job_id)
        try:
            async with sessionmaker() as db:
                job = await db.get(Job, job_id)
            if job is None:
                continue
            if store.converting(job.project_id):   # its characters before its prints (Ruling AN)
                await convert_project(sessionmaker, job.project_id, models_run, reference_prints)
            vocals = store.vocals_path(job)
            if not models_run or not vocals.exists():
                continue
            async with sessionmaker() as db:
                job = await db.get(Job, job_id)
                if job is None:
                    continue
                todo = (await db.execute(
                    select(Segment).outerjoin(LinePrint, LinePrint.segment_id == Segment.id)
                    .where(Segment.job_id == job_id, (LinePrint.segment_id.is_(None))
                           | (LinePrint.model_version != store.model_version()))
                    .order_by(Segment.start_time))).scalars().all()
                if not todo:   # a re-analysis since the queue was made gave them their prints
                    continue
                ids, spans = [s.id for s in todo], [actor_span(s) for s in todo]
                try:
                    found = await store.make_prints(db, job, spans, vocals)
                except store.CANT_RUN as e:
                    logger.warning(f"Older lines' prints weren't made, the models can't run here: {e}")
                    models_run = False
                    continue
                # lines the user deleted while the models ran keep no print
                alive = set((await db.execute(select(Segment.id).where(Segment.id.in_(ids)))).scalars())
                keep = [(sid, p) for sid, p in zip(ids, found) if sid in alive]
                await store.save_prints(db, job_id, [sid for sid, _ in keep], [p for _, p in keep],
                                        store.faces_dir(job))
                await db.commit()
                logger.info(f"Job {job_id[:8]}: {len(keep)} older line(s) got their prints")
        except Exception as e:  # noqa: BLE001 — not fatal: the next job goes on, the next launch tries this one again
            logger.exception(f"Couldn't make the prints of job {job_id[:8]}'s older lines: {e}")
        finally:
            store.backfill_done(job_id)
    return models_run


async def finish_upgrade(sessionmaker) -> None:
    """The background part of the upgrade, at every start-up: sweep stray crops; convert the projects that wait for
    it, each before its prints are made; make missing prints; then each project's thumbnails. Never raises, and
    leaves nothing queued: whatever happened, no line waits on work this session won't do."""
    try:
        await sweep_orphan_crops(sessionmaker)
    except Exception:  # noqa: BLE001 — not fatal: the next launch tries again
        logger.exception("Couldn't sweep the face crops no line uses")
    try:
        reference_prints: dict = {}   # the voices' reference clip prints, made once for the whole run
        models_run = await backfill_prints(sessionmaker, reference_prints)
        for project_id in store.conversions_pending():   # a project with no episode: no queued job reached it
            await convert_project(sessionmaker, project_id, models_run, reference_prints)
        async with sessionmaker() as db:
            project_ids = (await db.execute(select(Project.id))).scalars().all()
        for project_id in project_ids:
            async with sessionmaker() as db:
                await refresh_faces(db, project_id)
    except Exception:  # noqa: BLE001 — not fatal: the next launch tries again
        logger.exception("Couldn't finish upgrading older projects to characters")
    finally:
        store.clear_backfill()


async def upgrade_projects(sessionmaker) -> None:
    """The start-up's order: queue (the lifespan awaits it), then the background part."""
    await queue_backfill(sessionmaker)
    await finish_upgrade(sessionmaker)
