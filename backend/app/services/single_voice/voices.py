"""Voices and export of a single-voice dub episode: every line with Khmer and no current voice (none yet, its file gone,
or made from older text) is voiced with the project's Edge voice — through the same per-line voice step as the
editor's Generate Voice (routes/tts._synthesize_segment_db, which sends a single-voice project's lines to Edge) — a few
at a time, and then, only if every one has a voice, the episode is exported like Export does
(routes/tts._mix_job_task). Voices are kept as generated: nothing is sped up to fit (the owner: "I can apply it if it
feels slow"). Warnings the analysis left (lines the free fallback translated, a long video) stay on the episode."""
import asyncio
import logging

from sqlalchemy import select

from app.api.routes import tts as tts_routes
from app.core.paths import resolve_existing
from app.models.models import Job, JobStatus, Segment
from app.services.pipeline_common import update_job_status

logger = logging.getLogger(__name__)

CONCURRENCY = 4   # Edge throttles bursts; each line also renders its clip with ffmpeg


def _needs_voice(seg: Segment) -> bool:
    return bool((seg.khmer_text or "").strip()) and (
        not resolve_existing(seg.tts_audio_path) or seg.voice_text != seg.khmer_text)


async def voice_and_export(job_id: str, export_path: str | None) -> None:
    from app.core.database import AsyncSessionLocal

    warnings = ""
    try:
        async with AsyncSessionLocal() as db:
            job = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
            if job is None:
                return
            warnings = job.error_msg or ""
            lines = (await db.execute(select(Segment).where(Segment.job_id == job_id))).scalars().all()
            todo = [seg.id for seg in lines if _needs_voice(seg)]
            empty = sum(1 for seg in lines if not (seg.khmer_text or "").strip() and (seg.source_text or "").strip())
            if empty:
                warnings = " ".join(filter(None, [warnings, f"{empty} line{'s' if empty > 1 else ''} had no Khmer "
                                                            "and no voice."]))
            await update_job_status(db, job, JobStatus.SYNTHESIZING, 5, warnings)

        gate = asyncio.Semaphore(CONCURRENCY)
        done = {"n": 0}

        async def one(segment_id: str) -> bool:
            async with gate, AsyncSessionLocal() as s:
                try:
                    await tts_routes._synthesize_segment_db(segment_id, s)
                    await s.commit()
                    return True
                except Exception as e:   # noqa: BLE001 — Edge busy or gone: this line has no voice
                    logger.warning(f"Single-voice: line {segment_id[:8]} has no voice: {getattr(e, 'detail', e)}")
                    await s.rollback()
                    return False
                finally:
                    done["n"] += 1
                    row = (await s.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
                    if row is not None:
                        row.progress = 5 + int(90 * done["n"] / max(len(todo), 1))
                        await s.commit()

        failed = sum(1 for ok in await asyncio.gather(*(one(i) for i in todo)) if not ok)

        async with AsyncSessionLocal() as db:
            job = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one()
            if failed:
                await update_job_status(db, job, JobStatus.COMPLETED, 100, " ".join(filter(None, [
                    f"{failed} line{'s have' if failed > 1 else ' has'} no voice — Edge's voice service didn't "
                    "answer. Press Generate voices & export again to try them.", warnings])))
                return
            await update_job_status(db, job, JobStatus.MIXING, 5, warnings)
        await tts_routes._mix_job_task(job_id, True, export_path)

        if warnings:   # a clean export clears the message: put the analysis's warnings back
            async with AsyncSessionLocal() as db:
                job = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one()
                if job.status == JobStatus.COMPLETED and not job.error_msg:
                    job.error_msg = warnings
                    await db.commit()
    except Exception as e:   # noqa: BLE001 — never leave the episode busy until a restart
        logger.exception(f"Single-voice voices/export failed for job {job_id[:8]}: {e}")
        async with AsyncSessionLocal() as db:
            job = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
            if job is not None:
                await update_job_status(db, job, JobStatus.COMPLETED, 100,
                                        " ".join(filter(None, [f"Generate voices & export failed: {e}", warnings])))
