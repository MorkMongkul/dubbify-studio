"""Existing projects meet characters (0.5.0).

Before characters, the owner fixed a line whose voice sounded like another actor by giving the line its own Khmer
voice. `convert_voice_overrides` turns those voices into characters, once per project (`projects.characters_version`),
so recognition can learn them. Everything else about the upgrade runs at every start-up and can resume: the prints of
lines that have none (or stale ones), the sweep of face crops no print names, and each character's thumbnail.
Nothing here fails the start-up."""
import asyncio
import logging
import re
from collections import Counter
from contextlib import suppress
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.paths import resolve_media_path
from app.models.models import AgeGroup, Gender, Job, LinePrint, Project, Segment, Speaker
from app.services.characters import store
from app.services.diarizer import build_voice_design_prompt
from app.services.timeline import actor_span

logger = logging.getLogger(__name__)

CHARACTERS_VERSION = 1


def _next_label(taken: set[str]) -> str:
    """The next free SPEAKER_XX: after the highest number ever used here, so a label a merge removed isn't reused
    (the Story notes may still link a character to it)."""
    numbers = [int(m.group(1)) for label in taken if (m := re.fullmatch(r"SPEAKER_(\d+)", label))]
    n = max(numbers, default=-1) + 1
    while f"SPEAKER_{n:02d}" in taken:
        n += 1
    return f"SPEAKER_{n:02d}"


async def convert_voice_overrides(db: AsyncSession, project_id: str) -> int:
    """Every line whose own voice differs from its speaker's (or that has no speaker) moves to the character a person
    cast with that voice — the one with the most lines when several, the lowest label on a tie — or to a new
    character for a voice no person gave anyone (one per voice, no name). A voice "Choose for me" picked
    (voice_auto) says nothing about who the actor is: it repeats on unrelated characters. The line's own voice is
    cleared and the line counts as confirmed: it was the owner's hand fix. Text, timing and audio are untouched.
    Flushes; the caller commits. Returns how many lines moved."""
    speakers = (await db.execute(select(Speaker).where(Speaker.project_id == project_id))).scalars().all()
    by_id = {sp.id: sp for sp in speakers}
    lines = (await db.execute(
        select(Segment).join(Job, Job.id == Segment.job_id).where(Job.project_id == project_id)
    )).scalars().all()
    overrides = [s for s in lines if s.voice_id and s.voice_id != (by_id[s.speaker_id].voice_id
                                                                   if s.speaker_id in by_id else None)]
    if not overrides:
        return 0
    counts = Counter(s.speaker_id for s in lines)
    taken = {sp.label for sp in speakers}
    target_of: dict[str, Speaker] = {}
    for voice_id in sorted({s.voice_id for s in overrides}):
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
    for line in overrides:
        line.speaker_id = target_of[line.voice_id].id
        line.voice_id = None
        line.who_confirmed, line.who_check = True, None
    await db.flush()
    return len(overrides)


async def convert_projects(sessionmaker) -> int:
    """Convert each project not yet upgraded (characters_version NULL), one commit per project, so a restart
    part-way never converts one twice. Quick database work: the lifespan awaits it. Returns lines moved."""
    async with sessionmaker() as db:
        project_ids = (await db.execute(select(Project.id).where(Project.characters_version.is_(None)))).scalars().all()
    total = 0
    for project_id in project_ids:
        try:
            async with sessionmaker() as db:
                project = await db.get(Project, project_id)
                if project is None or project.characters_version is not None:
                    continue
                moved = await convert_voice_overrides(db, project_id)
                project.characters_version = CHARACTERS_VERSION
                await db.commit()
                total += moved
                if moved:
                    logger.info(f"Project {project_id[:8]}: {moved} line(s) with their own voice moved to characters")
        except Exception:  # noqa: BLE001 — not fatal: the project stays unconverted and the next launch retries
            logger.exception(f"Couldn't turn project {project_id[:8]}'s one-line voices into characters")
    return total


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


async def _jobs_needing_prints(db: AsyncSession) -> list[str]:
    """Jobs with a line that has no print from today's models: the project worked on most recently first (by its
    newest episode; until its profiles are complete, no line of its next episode can be sure), each project's
    newest episodes first."""
    newest = (select(Job.project_id, func.max(Job.created_at).label("newest"))
              .group_by(Job.project_id).subquery())
    waiting = (select(Segment.job_id).outerjoin(LinePrint, LinePrint.segment_id == Segment.id)
               .where((LinePrint.segment_id.is_(None)) | (LinePrint.model_version != store.model_version())))
    return (await db.execute(
        select(Job.id).join(newest, newest.c.project_id == Job.project_id).where(Job.id.in_(waiting))
        .order_by(newest.c.newest.desc(), Job.project_id, Job.created_at.desc(), Job.id)
    )).scalars().all()


async def backfill_prints(sessionmaker) -> bool:
    """Prints for every line with none (or made by other models), job by job through the one-at-a-time lock.
    The models run with no connection held (`store.make_prints` commits first). Returns False when they can't run
    here (logged once): the rest waits for a Mac that can. An episode whose prints fail is logged and skipped, so
    one bad episode never keeps the others from theirs."""
    async with sessionmaker() as db:
        job_ids = await _jobs_needing_prints(db)
    for job_id in job_ids:
        try:
            async with sessionmaker() as db:
                job = await db.get(Job, job_id)
                vocals = store._vocals_of(job) if job else None
                if vocals is None:
                    continue
                todo = (await db.execute(
                    select(Segment).outerjoin(LinePrint, LinePrint.segment_id == Segment.id)
                    .where(Segment.job_id == job_id, (LinePrint.segment_id.is_(None))
                           | (LinePrint.model_version != store.model_version()))
                    .order_by(Segment.start_time))).scalars().all()
                ids, spans = [s.id for s in todo], [actor_span(s) for s in todo]
                try:
                    found = await store.make_prints(db, job, spans, vocals)
                except store.CANT_RUN as e:
                    logger.warning(f"Older lines' prints weren't made, the models can't run here: {e}")
                    return False
                # lines the user deleted while the models ran keep no print
                alive = set((await db.execute(select(Segment.id).where(Segment.id.in_(ids)))).scalars())
                keep = [(sid, p) for sid, p in zip(ids, found) if sid in alive]
                await store.save_prints(db, job_id, [sid for sid, _ in keep], [p for _, p in keep],
                                        store.faces_dir(job))
                await db.commit()
                logger.info(f"Job {job_id[:8]}: {len(keep)} older line(s) got their prints")
        except Exception as e:  # noqa: BLE001 — not fatal: the next job goes on, the next launch tries this one again
            logger.exception(f"Couldn't make the prints of job {job_id[:8]}'s older lines: {e}")
    return True


async def finish_upgrade(sessionmaker) -> None:
    """The background part of the upgrade, at every start-up: sweep stray crops, make missing prints, then each
    project's thumbnails. Never raises."""
    try:
        await sweep_orphan_crops(sessionmaker)
        await backfill_prints(sessionmaker)
        async with sessionmaker() as db:
            project_ids = (await db.execute(select(Project.id))).scalars().all()
        for project_id in project_ids:
            async with sessionmaker() as db:
                await refresh_faces(db, project_id)
    except Exception:  # noqa: BLE001 — not fatal: the next launch tries again
        logger.exception("Couldn't finish upgrading older projects to characters")


async def upgrade_projects(sessionmaker) -> None:
    """Both parts in order (the lifespan awaits the first and runs the second in the background)."""
    await convert_projects(sessionmaker)
    await finish_upgrade(sessionmaker)
