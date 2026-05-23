"""
app/services/subtitle_pipeline.py
Pipeline for jobs that have a subtitle file.

Much faster and more accurate than ASR pipeline:
  1. Extract audio     (ffmpeg — for speaker diarization only)
  2. Parse subtitles   (SRT or ASS — exact text, exact timing)
  3. Diarize audio     (pyannoteAI — speaker identification only, no transcription)
  4. Match speakers    (assign speakers to subtitle lines by timestamp overlap)
  5. Translate         (Gemini — ZH→EN + ZH→KM with full context)
  6. Save to DB

Skips: Whisper ASR entirely — subtitles are always more accurate.
"""
import asyncio
import logging
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.models.models import Job, Project, Speaker, Segment, JobStatus, Gender, AgeGroup
from app.services.audio_extractor import extract_audio, get_video_duration
from app.services.subtitle_parser import parse_subtitle_file, assign_speakers_by_timing
from app.services.diarizer import diarize_audio, build_voice_design_prompt
from app.services.translator import translate_batch

logger = logging.getLogger(__name__)


async def _update_job(db, job, status, progress, error_msg=""):
    job.status    = status
    job.progress  = progress
    job.error_msg = error_msg
    await db.flush()
    logger.info(f"Job {job.id[:8]} | {status.value} | {progress}%")


async def run_subtitle_pipeline(
    job_id: str,
    subtitle_path: str,
    db: AsyncSession,
) -> None:
    """
    Execute subtitle-based pipeline for a job.

    Args:
        job_id:        UUID of the Job record
        subtitle_path: Path to .srt or .ass file on disk
        db:            Async SQLAlchemy session
    """
    # ── Load job + project ────────────────────────────────────
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        logger.error(f"Job {job_id} not found")
        return

    result = await db.execute(select(Project).where(Project.id == job.project_id))
    project = result.scalar_one_or_none()
    if not project:
        await _update_job(db, job, JobStatus.FAILED, 0, "Project not found")
        return

    job_dir = Path(settings.UPLOAD_DIR) / job.project_id / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    try:
        # ── STAGE 1: Parse subtitle file ──────────────────────
        await _update_job(db, job, JobStatus.EXTRACTING, 10)

        logger.info(f"Parsing subtitle file: {subtitle_path}")
        subtitle_segments = parse_subtitle_file(subtitle_path)
        logger.info(f"Found {len(subtitle_segments)} subtitle lines")

        # ── STAGE 2: Extract audio for diarization ────────────
        await _update_job(db, job, JobStatus.EXTRACTING, 20)

        # Get video duration
        try:
            duration = await get_video_duration(job.video_path)
            job.duration_secs = duration
        except Exception as e:
            logger.warning(f"Could not get video duration: {e}")

        audio_path = await extract_audio(
            video_path=job.video_path,
            output_dir=str(job_dir),
        )
        job.audio_path = audio_path
        await db.flush()

        # ── STAGE 3: Speaker diarization (no transcription) ───
        await _update_job(db, job, JobStatus.DIARIZING, 35)

        # Run diarization-only (transcription=False saves credits)
        # We already have the text from subtitles
        diarized = await diarize_audio(audio_path)

        # ── STAGE 4: Match speakers to subtitle lines ─────────
        await _update_job(db, job, JobStatus.TRANSCRIBING, 50)

        # Assign speaker labels by timestamp overlap
        subtitle_segments = assign_speakers_by_timing(subtitle_segments, diarized)

        # Build Speaker records — one per unique speaker
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

        # Log speaker assignment results
        speaker_counts = {}
        for sub in subtitle_segments:
            speaker_counts[sub.speaker_label] = speaker_counts.get(sub.speaker_label, 0) + 1
        logger.info(f"Speaker assignment: {speaker_counts}")

        # ── STAGE 5: Translate ────────────────────────────────
        await _update_job(db, job, JobStatus.TRANSLATING, 60)

        source_texts = [seg.text for seg in subtitle_segments]

        # Build context for Gemini — subtitle text is cleaner than ASR
        segments_context = [
            {
                "speaker_label": seg.speaker_label or "SPEAKER_00",
                "source_text": seg.text,
            }
            for seg in subtitle_segments
        ]

        logger.info(f"Translating {len(source_texts)} subtitle lines...")

        en_texts, km_texts = await asyncio.gather(
            translate_batch(
                source_texts, project.source_lang, "en",
                segments_context=segments_context,
            ),
            translate_batch(
                source_texts, project.source_lang, project.target_lang,
                segments_context=segments_context,
            ),
        )

        # ── STAGE 6: Save segments to DB ──────────────────────
        await _update_job(db, job, JobStatus.TRANSLATING, 85)

        for i, sub in enumerate(subtitle_segments):
            speaker = speaker_map.get(sub.speaker_label)
            segment = Segment(
                job_id=job.id,
                speaker_id=speaker.id if speaker else None,
                start_time=sub.start_time,
                end_time=sub.end_time,
                source_text=sub.text,
                english_text=en_texts[i] if i < len(en_texts) else "",
                khmer_text=km_texts[i] if i < len(km_texts) else "",
            )
            db.add(segment)

        await db.flush()

        # ── DONE ─────────────────────────────────────────────
        await _update_job(db, job, JobStatus.COMPLETED, 100)
        logger.info(
            f"Subtitle pipeline complete for job {job_id[:8]} — "
            f"{len(subtitle_segments)} lines processed."
        )

    except Exception as e:
        logger.exception(f"Subtitle pipeline failed for job {job_id[:8]}: {e}")
        await _update_job(db, job, JobStatus.FAILED, 0, str(e))
        raise
