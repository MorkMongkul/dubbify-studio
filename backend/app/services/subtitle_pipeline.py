"""
app/services/subtitle_pipeline.py
Subtitle-based pipeline — two user-controlled stages.

Stage 1  run_subtitle_pipeline()          → parse subtitles + extract + separate → STEMS_READY
Stage 2  run_subtitle_analysis_pipeline() → diarize + match speakers + translate → COMPLETED

Skips Whisper ASR entirely — subtitles are always more accurate than ASR.
"""
import logging
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.models.models import Job, Project, Segment, JobStatus
from app.services.audio_extractor import extract_audio, get_video_duration
from app.services.source_separator import separate_vocals_bgm
from app.services.subtitle_parser import parse_subtitle_file, assign_speakers_by_timing
from app.services.diarizer import diarize_audio
from app.services.translator import translate_batch
from app.services.pipeline_common import (
    update_job_status as _update_job,
    get_or_create_speakers,
)

logger = logging.getLogger(__name__)


# ── STAGE 1: Parse subtitles + extract audio + separate ──────────────────────

async def run_subtitle_pipeline(job_id: str, subtitle_path: str) -> None:
    """
    Stage 1 for subtitle jobs: parse SRT/ASS, extract audio, run Demucs.
    Stores parsed subtitle text as Segments (no speakers yet) and stops at STEMS_READY.
    """
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
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
            await _update_job(db, job, JobStatus.EXTRACTING, 5)

            logger.info(f"Parsing subtitle file: {subtitle_path}")
            subtitle_segments = parse_subtitle_file(subtitle_path)
            logger.info(f"Found {len(subtitle_segments)} subtitle lines")

            try:
                job.duration_secs = await get_video_duration(job.video_path)
            except Exception as e:
                logger.warning(f"Could not get video duration: {e}")

            audio_path = await extract_audio(job.video_path, output_dir=str(job_dir))
            job.audio_path = audio_path
            await db.commit()

            await _update_job(db, job, JobStatus.SEPARATING, 20)
            vocals_path, _bgm = await separate_vocals_bgm(audio_path, str(job_dir))
            logger.info(f"Separation complete — vocals: {vocals_path}")

            # Store subtitle text as Segments without speakers so the
            # user can see the script while the stems play on the timeline.
            # Speakers are assigned in Stage 2 after diarization.
            await _update_job(db, job, JobStatus.STEMS_READY, 30)
            for sub in subtitle_segments:
                db.add(Segment(
                    job_id=job.id,
                    speaker_id=None,
                    start_time=sub.start_time,
                    end_time=sub.end_time,
                    source_text=sub.text,
                    english_text="",
                    khmer_text="",
                ))
            await db.commit()

            logger.info(f"Job {job_id[:8]} STEMS_READY — {len(subtitle_segments)} subtitle lines saved, waiting for Analyze")

        except Exception as e:
            logger.exception(f"Subtitle Stage 1 failed for job {job_id[:8]}: {e}")
            await _update_job(db, job, JobStatus.FAILED, 0, str(e))


# ── STAGE 2: Diarize + match speakers + translate ─────────────────────────────

async def run_subtitle_analysis_pipeline(job_id: str, max_speakers: int | None = None) -> None:
    """
    Stage 2 for subtitle jobs: diarize vocals.wav, assign speakers to existing
    Segments, then translate. Triggered by user clicking Analyze.

    max_speakers: optional cap on diarization speakers (curbs over-clustering).
    """
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
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
        vocals_path = str(job_dir / "vocals.wav")
        if not Path(vocals_path).exists():
            vocals_path = job.audio_path

        try:
            await _update_job(db, job, JobStatus.DIARIZING, 35)
            diarized = await diarize_audio(vocals_path, max_speakers=max_speakers)

            # Build Speaker records (idempotent — reuses existing project speakers)
            speaker_map = await get_or_create_speakers(db, project.id, diarized)

            await _update_job(db, job, JobStatus.TRANSCRIBING, 50)

            # Load existing subtitle segments from DB and assign speakers by timing
            seg_result = await db.execute(
                select(Segment).where(Segment.job_id == job_id).order_by(Segment.start_time)
            )
            existing_segments = seg_result.scalars().all()

            # Re-create SubtitleSegment-like objects for the timing matcher
            class _Sub:
                def __init__(self, seg): self.start_time = seg.start_time; self.end_time = seg.end_time; self.text = seg.source_text; self.speaker_label = None

            sub_objs = [_Sub(s) for s in existing_segments]
            sub_objs = assign_speakers_by_timing(sub_objs, diarized)

            # Update segments with assigned speakers
            for sub, db_seg in zip(sub_objs, existing_segments):
                speaker = speaker_map.get(sub.speaker_label)
                db_seg.speaker_id = speaker.id if speaker else None
            await db.commit()

            # Translate
            await _update_job(db, job, JobStatus.TRANSLATING, 65)
            source_texts = [s.source_text for s in existing_segments]
            segments_context = [
                {"speaker_label": sub.speaker_label or "SPEAKER_00", "source_text": sub.text}
                for sub in sub_objs
            ]

            # Translate source → target (Khmer) DIRECTLY — no English pivot.
            km_texts = await translate_batch(source_texts, project.source_lang, project.target_lang, segments_context=segments_context)

            await _update_job(db, job, JobStatus.TRANSLATING, 85)
            for i, db_seg in enumerate(existing_segments):
                db_seg.english_text = ""
                db_seg.khmer_text   = km_texts[i] if i < len(km_texts) else ""
            await db.commit()

            await _update_job(db, job, JobStatus.COMPLETED, 100)
            logger.info(f"Subtitle analysis complete for job {job_id[:8]} — {len(existing_segments)} segments.")

        except Exception as e:
            logger.exception(f"Subtitle Stage 2 failed for job {job_id[:8]}: {e}")
            await _update_job(db, job, JobStatus.FAILED, 0, str(e))
