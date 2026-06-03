"""
app/services/pipeline_common.py
Shared helpers used by both the ASR pipeline and the subtitle pipeline.

Extracted to remove the duplicated _update_job and speaker-creation logic
that previously lived in both pipeline modules.
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.models.models import Job, Speaker, Segment, JobStatus, Gender, AgeGroup
from app.services.diarizer import build_voice_design_prompt

logger = logging.getLogger(__name__)


async def update_job_status(
    db: AsyncSession, job: Job, status: JobStatus, progress: int, error_msg: str = ""
) -> None:
    """Persist a job's status/progress and log the transition."""
    job.status    = status
    job.progress  = progress
    job.error_msg = error_msg
    await db.commit()
    logger.info(f"Job {job.id[:8]} | {status.value} | {progress}%")


async def get_or_create_speakers(
    db: AsyncSession, project_id: str, diarized: list
) -> dict[str, Speaker]:
    """
    Build a {speaker_label: Speaker} map for the project, reusing speakers
    that already exist (matched by label).

    Idempotent: re-running Stage 2 will not create duplicate Speaker rows.
    Speakers are project-scoped (matching the data model), so labels are
    shared across jobs within the same project.
    """
    result = await db.execute(select(Speaker).where(Speaker.project_id == project_id))
    speaker_map: dict[str, Speaker] = {sp.label: sp for sp in result.scalars().all()}

    for seg in diarized:
        label = seg.speaker_label
        if label not in speaker_map:
            speaker = Speaker(
                project_id=project_id,
                label=label,
                display_name=label,
                gender=Gender(seg.gender),
                age_group=AgeGroup(seg.age_group),
                voice_design_prompt=build_voice_design_prompt(label, seg.gender, seg.age_group),
            )
            db.add(speaker)
            await db.commit()
            speaker_map[label] = speaker
    return speaker_map


async def clear_job_segments(db: AsyncSession, job_id: str) -> None:
    """
    Delete existing segments for a job so a Stage 2 re-run does not stack up
    duplicate rows. Only used by the ASR pipeline, which creates segments
    fresh — the subtitle pipeline updates its segments in place.
    """
    await db.execute(delete(Segment).where(Segment.job_id == job_id))
    await db.commit()


# Statuses that mean a job was actively being processed in a worker.
# If the server restarts, these can never resume on their own (work runs in
# in-process BackgroundTasks), so they must be failed on startup.
# STEMS_READY is a valid paused state and is intentionally excluded.
_ORPHANABLE_STATUSES = [
    JobStatus.PENDING,
    JobStatus.EXTRACTING,
    JobStatus.SEPARATING,
    JobStatus.DIARIZING,
    JobStatus.TRANSCRIBING,
    JobStatus.TRANSLATING,
    JobStatus.SYNTHESIZING,
    JobStatus.MIXING,
]


async def recover_orphaned_jobs() -> None:
    """
    Mark any job left mid-processing by a previous run as failed.

    Pipeline work runs in FastAPI BackgroundTasks inside this process, so a
    restart abandons in-flight jobs — they would otherwise sit forever showing
    'diarizing', 'extracting', etc. with no worker behind them.

    Filtering is done in Python rather than via a SQL WHERE on the status enum:
    the live Postgres `jobs.status` column is plain VARCHAR, so an enum-typed
    comparison emits a `::jobstatus` cast that has no matching operator. Reading
    the rows and comparing the ORM-converted JobStatus members sidesteps that,
    and the per-row assignment matches how the pipeline already updates status.
    """
    from app.core.database import AsyncSessionLocal

    orphanable = set(_ORPHANABLE_STATUSES)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Job))
        recovered = 0
        for job in result.scalars().all():
            if job.status in orphanable:
                job.status = JobStatus.FAILED
                job.error_msg = "Interrupted by server restart"
                recovered += 1
        if recovered:
            await db.commit()
            logger.warning(f"Recovered {recovered} orphaned job(s) → failed")
