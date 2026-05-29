"""
app/services/pipeline.py
ASR pipeline orchestrator — two user-controlled stages.

Stage 1  run_pipeline()           → extract audio + demucs separation → STEMS_READY
Stage 2  run_analysis_pipeline()  → diarize + transcribe + translate  → COMPLETED

TTS synthesis and final mix are triggered separately per-segment / on demand.
"""
import logging
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.models.models import Job, Project, Speaker, Segment, JobStatus, Gender, AgeGroup
from app.services.audio_extractor import extract_audio, get_video_duration
from app.services.source_separator import separate_vocals_bgm
from app.services.diarizer import diarize_audio, build_voice_design_prompt
from app.services.translator import translate_batch

logger = logging.getLogger(__name__)


async def _update_job(db: AsyncSession, job: Job, status: JobStatus, progress: int, error_msg: str = "") -> None:
    job.status    = status
    job.progress  = progress
    job.error_msg = error_msg
    await db.commit()
    logger.info(f"Job {job.id[:8]} | {status.value} | {progress}%")


# ── STAGE 1: Extract audio + separate stems ───────────────────────────────────

async def run_pipeline(job_id: str) -> None:
    """
    Stage 1: extract audio from video + run Demucs separation.
    Stops at STEMS_READY so the user can review stems before analysis.
    """
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()
        if not job:
            logger.error(f"Job {job_id} not found.")
            return

        job_dir = Path(settings.UPLOAD_DIR) / job.project_id / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        try:
            await _update_job(db, job, JobStatus.EXTRACTING, 5)

            try:
                job.duration_secs = await get_video_duration(job.video_path)
            except Exception as e:
                logger.warning(f"Could not get video duration: {e}")
                job.duration_secs = 0.0

            audio_path = await extract_audio(job.video_path, output_dir=str(job_dir))
            job.audio_path = audio_path
            await db.commit()

            await _update_job(db, job, JobStatus.SEPARATING, 20)
            vocals_path, _bgm = await separate_vocals_bgm(audio_path, str(job_dir))
            logger.info(f"Separation complete — vocals: {vocals_path}")

            # Stage 1 done — hand off to user
            await _update_job(db, job, JobStatus.STEMS_READY, 30)
            logger.info(f"Job {job_id[:8]} is STEMS_READY — waiting for user to click Analyze")

        except Exception as e:
            logger.exception(f"Stage 1 failed for job {job_id[:8]}: {e}")
            await _update_job(db, job, JobStatus.FAILED, 0, str(e))


# ── STAGE 2: Diarize + transcribe + translate ─────────────────────────────────

async def run_analysis_pipeline(job_id: str) -> None:
    """
    Stage 2: diarize vocals.wav, transcribe, translate.
    Triggered by the user clicking Analyze in the editor.
    """
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()
        if not job:
            logger.error(f"Job {job_id} not found.")
            return

        result = await db.execute(select(Project).where(Project.id == job.project_id))
        project = result.scalar_one_or_none()
        if not project:
            await _update_job(db, job, JobStatus.FAILED, 0, "Project not found.")
            return

        job_dir = Path(settings.UPLOAD_DIR) / job.project_id / job_id
        vocals_path = str(job_dir / "vocals.wav")

        if not Path(vocals_path).exists():
            # Fallback: use original audio if separation didn't run
            vocals_path = job.audio_path

        try:
            await _update_job(db, job, JobStatus.DIARIZING, 35)
            diarized = await diarize_audio(vocals_path)

            # Build Speaker records
            speaker_map: dict[str, Speaker] = {}
            for seg in diarized:
                label = seg.speaker_label
                if label not in speaker_map:
                    speaker = Speaker(
                        project_id=project.id,
                        label=label,
                        display_name=label,
                        gender=Gender(seg.gender),
                        age_group=AgeGroup(seg.age_group),
                        voice_design_prompt=build_voice_design_prompt(label, seg.gender, seg.age_group),
                    )
                    db.add(speaker)
                    await db.commit()
                    speaker_map[label] = speaker

            await _update_job(db, job, JobStatus.TRANSCRIBING, 55)
            has_text = sum(1 for s in diarized if s.source_text)
            logger.info(f"Transcription: {has_text}/{len(diarized)} segments have text")

            await _update_job(db, job, JobStatus.TRANSLATING, 65)
            source_texts = [seg.source_text for seg in diarized]
            segments_context = [
                {"speaker_label": seg.speaker_label, "source_text": seg.source_text}
                for seg in diarized
            ]

            logger.info(f"Translating {len(source_texts)} segments to English...")
            en_texts = await translate_batch(source_texts, project.source_lang, "en", segments_context=segments_context)

            logger.info(f"Translating {len(source_texts)} segments to {project.target_lang}...")
            km_texts = await translate_batch(source_texts, project.source_lang, project.target_lang, segments_context=segments_context)

            await _update_job(db, job, JobStatus.TRANSLATING, 85)

            for i, seg in enumerate(diarized):
                speaker = speaker_map.get(seg.speaker_label)
                db.add(Segment(
                    job_id=job.id,
                    speaker_id=speaker.id if speaker else None,
                    start_time=seg.start_time,
                    end_time=seg.end_time,
                    source_text=seg.source_text,
                    english_text=en_texts[i] if i < len(en_texts) else "",
                    khmer_text=km_texts[i] if i < len(km_texts) else "",
                ))
            await db.commit()

            await _update_job(db, job, JobStatus.COMPLETED, 100)
            logger.info(f"Analysis complete for job {job_id[:8]} — {len(diarized)} segments.")

        except Exception as e:
            logger.exception(f"Stage 2 failed for job {job_id[:8]}: {e}")
            await _update_job(db, job, JobStatus.FAILED, 0, str(e))
