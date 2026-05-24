"""
app/services/pipeline.py
Master pipeline orchestrator.

Stage order:
  1. Extract audio      (ffmpeg)
  2. Diarize + Transcribe (pyannoteAI — one call does both)
  3. Translate          (deep-translator or NLLB-200)
  4. TTS synthesis      (VoxCPM2)   ← triggered separately per segment
  5. Mix final audio    (ffmpeg)    ← triggered separately after TTS approval
"""
import logging
import os
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.models.models import Job, Project, Speaker, Segment, JobStatus, Gender, AgeGroup
from app.services.audio_extractor import extract_audio, get_video_duration
from app.services.diarizer import diarize_audio, build_voice_design_prompt
from app.services.translator import translate_text, translate_batch

logger = logging.getLogger(__name__)


async def _update_job(
    db: AsyncSession,
    job: Job,
    status: JobStatus,
    progress: int,
    error_msg: str = "",
):
    """Helper: update job status + progress in DB and flush immediately."""
    job.status    = status
    job.progress  = progress
    job.error_msg = error_msg
    await db.flush()
    logger.info(f"Job {job.id[:8]} | {status.value} | {progress}%")


async def run_pipeline(job_id: str, db: AsyncSession) -> None:
    """
    Execute the full ASR + Translation pipeline for a job.

    This function is designed to run as a background task (via Celery or
    FastAPI BackgroundTasks). It updates job.status at each stage so the
    frontend can show a live progress bar.

    Args:
        job_id: UUID of the Job record
        db:     Async SQLAlchemy session
    """
    # ── Load job + project ────────────────────────────────────
    result = await db.execute(
        select(Job).where(Job.id == job_id)
    )
    job = result.scalar_one_or_none()
    if not job:
        logger.error(f"Job {job_id} not found.")
        return

    result = await db.execute(
        select(Project).where(Project.id == job.project_id)
    )
    project = result.scalar_one_or_none()
    if not project:
        await _update_job(db, job, JobStatus.FAILED, 0, "Project not found.")
        return

    # Prepare output directory for this job
    job_dir = Path(settings.UPLOAD_DIR) / job.project_id / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    try:
        # ── STAGE 1: Audio extraction ─────────────────────────
        await _update_job(db, job, JobStatus.EXTRACTING, 5)

        # Get duration — non-fatal if ffprobe not installed
        try:
            duration = await get_video_duration(job.video_path)
            job.duration_secs = duration
        except Exception as e:
            logger.warning(f"Could not get video duration: {e} — continuing anyway")
            job.duration_secs = 0.0

        audio_path = await extract_audio(
            video_path=job.video_path,
            output_dir=str(job_dir),
        )
        job.audio_path = audio_path
        await db.flush()

        # ── STAGE 2: Diarization + Transcription (pyannoteAI) ────
        # pyannoteAI returns BOTH speaker segments AND transcribed text
        # in one call — no separate Whisper/Groq step needed
        await _update_job(db, job, JobStatus.DIARIZING, 20)

        diarized = await diarize_audio(audio_path)

        # Build Speaker records — one per unique speaker label
        speaker_map: dict[str, Speaker] = {}
        for seg in diarized:
            label = seg.speaker_label
            if label not in speaker_map:
                voice_prompt = build_voice_design_prompt(
                    label, seg.gender, seg.age_group
                )
                speaker = Speaker(
                    project_id=project.id,
                    label=label,
                    display_name=label,
                    gender=Gender(seg.gender),
                    age_group=AgeGroup(seg.age_group),
                    voice_design_prompt=voice_prompt,
                )
                db.add(speaker)
                await db.flush()
                speaker_map[label] = speaker

        # ── STAGE 3: Mark transcribing (text comes from pyannoteAI) ──
        await _update_job(db, job, JobStatus.TRANSCRIBING, 50)

        has_text = sum(1 for s in diarized if s.source_text)
        logger.info(
            f"Transcription from pyannoteAI: {has_text}/{len(diarized)} segments have text"
        )

        # ── STAGE 4: Translation ──────────────────────────────
        await _update_job(db, job, JobStatus.TRANSLATING, 60)

        source_texts = [seg.source_text for seg in diarized]

        # Build context list for Gemini — includes speaker label per segment
        # so Gemini knows who is speaking when translating each line
        segments_context = [
            {
                "speaker_label": seg.speaker_label,
                "source_text": seg.source_text,
            }
            for seg in diarized
        ]

        # Translate EN then KM sequentially — never simultaneously
        # Simultaneous calls double the API pressure and cause 429s
        import asyncio
        logger.info(f"Translating {len(source_texts)} segments to English...")
        en_texts = await translate_batch(
            source_texts, project.source_lang, "en",
            segments_context=segments_context,
        )

        logger.info(f"Translating {len(source_texts)} segments to Khmer...")
        km_texts = await translate_batch(
            source_texts, project.source_lang, project.target_lang,
            segments_context=segments_context,
        )

        # ── STAGE 5: Save all segments to DB ─────────────────
        await _update_job(db, job, JobStatus.TRANSLATING, 80)

        for i, seg in enumerate(diarized):
            speaker = speaker_map.get(seg.speaker_label)
            segment = Segment(
                job_id=job.id,
                speaker_id=speaker.id if speaker else None,
                start_time=seg.start_time,
                end_time=seg.end_time,
                source_text=seg.source_text,
                english_text=en_texts[i] if i < len(en_texts) else "",
                khmer_text=km_texts[i] if i < len(km_texts) else "",
            )
            db.add(segment)

        await db.flush()

        # ── DONE ─────────────────────────────────────────────
        await _update_job(db, job, JobStatus.COMPLETED, 100)
        logger.info(f"Pipeline complete for job {job_id[:8]} — "
                    f"{len(diarized)} segments processed.")

    except Exception as e:
        logger.exception(f"Pipeline failed for job {job_id[:8]}: {e}")
        await _update_job(db, job, JobStatus.FAILED, 0, str(e))
        raise