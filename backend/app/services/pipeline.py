"""
app/services/pipeline.py
ASR pipeline orchestrator — two user-controlled stages.

Stage 1  run_pipeline()           → extract audio + stem separation (HF Space) → STEMS_READY
Stage 2  run_analysis_pipeline()  → transcribe + speaker-label + translate  → COMPLETED

Speaker labels come from app.services.asr (Gemini comparing voices across
clips in one request) rather than a dedicated diarization model like MOSS's
— treat them as an approximate starting point, not ground truth. The user
splits/reassigns speakers manually in the editor for anything wrong.

TTS synthesis and final mix are triggered separately per-segment / on demand.
"""
import logging
import tempfile
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.models.models import Job, Project, Segment, JobStatus
from app.services.audio_extractor import extract_audio, get_video_duration
from app.services.source_separator import separate_vocals_bgm
from app.services.asr import labels_for_spans, transcribe_audio
from app.services.translator import translate_batch_with_report
from app.services.pipeline_common import (
    update_job_status as _update_job,
    build_speaker_references,
    ensure_speakers,
    project_speaker_labels,
    remove_unused_auto_speakers,
    replace_job_segments,
    restore_or_fail_job,
    vocals_path_for,
)

logger = logging.getLogger(__name__)


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
            vocals_path, _bgm, degraded = await separate_vocals_bgm(audio_path, str(job_dir))
            logger.info(f"Separation complete — vocals: {vocals_path}")

            # The extracted full-mix WAV (~10MB/min) is only needed as a
            # diarization fallback for jobs where separation produced nothing —
            # once real stems exist, reclaim it.
            if vocals_path != audio_path and Path(vocals_path).exists():
                Path(audio_path).unlink(missing_ok=True)

            # Stage 1 done — hand off to user. `degraded` means the separation
            # Space failed and vocals/BGM are both just copies of the original
            # mixed audio — dialogue will bleed into the BGM bed, and ASR in
            # Stage 2 runs on unseparated audio.
            warning = (
                "Vocal/music separation was unavailable — using the original mixed "
                "audio for both the Vocals and BGM tracks. Speech recognition "
                "accuracy may be reduced, and BGM will contain some dialogue."
                if degraded else ""
            )
            await _update_job(db, job, JobStatus.STEMS_READY, 30, warning)
            if degraded:
                logger.warning(f"Job {job_id[:8]} is STEMS_READY with DEGRADED (unseparated) stems")
            else:
                logger.info(f"Job {job_id[:8]} is STEMS_READY — waiting for user to click Analyze")

        except Exception as e:
            logger.exception(f"Stage 1 failed for job {job_id[:8]}: {e}")
            await _update_job(db, job, JobStatus.FAILED, 0, str(e))


# ── STAGE 2: Transcribe + speaker-label + translate ───────────────────────────

async def _load_job_and_project(db: AsyncSession, job_id: str):
    job = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
    if not job:
        logger.error(f"Job {job_id} not found.")
        return None, None
    project = (await db.execute(select(Project).where(Project.id == job.project_id))).scalar_one_or_none()
    if not project:
        await _update_job(db, job, JobStatus.FAILED, 0, "Project not found.")
        return job, None
    return job, project


async def run_analysis_pipeline(job_id: str, max_speakers: int | None = None) -> None:
    """
    Stage 2: transcribe + speaker-label the vocals, translate, then swap the
    new segments in. Triggered by Analyze (first run) or Re-analyze.

    Nothing is deleted until the new segments are fully built: a failure at
    any point leaves the previous segments — and the user's edits — in place.

    max_speakers: accepted for API compatibility, ignored — Gemini decides
    how many distinct voices it hears (see app.services.asr).
    """
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        job, project = await _load_job_and_project(db, job_id)
        if not project:
            return
        try:
            vocals_path = vocals_path_for(job)
            if not vocals_path:
                raise RuntimeError("No extracted audio found for this session — upload the video again.")

            await _update_job(db, job, JobStatus.DIARIZING, 35)
            taken_labels = await project_speaker_labels(db, project.id)
            with tempfile.TemporaryDirectory(prefix="speaker_refs_") as ref_dir:
                references = await build_speaker_references(db, project.id, ref_dir)
                # Release the connection before minutes of Gemini calls — the dev
                # SQLite engine has exactly one.
                await db.commit()
                diarized, used_mock = await transcribe_audio(
                    vocals_path,
                    references=references,
                    taken_labels=taken_labels,
                    language=project.source_lang or "zh",
                )

            await _update_job(db, job, JobStatus.TRANSCRIBING, 55)
            has_text = sum(1 for s in diarized if s.source_text)
            logger.info(f"Transcription: {has_text}/{len(diarized)} segments have text")

            await _update_job(db, job, JobStatus.TRANSLATING, 65)
            source_texts = [seg.source_text for seg in diarized]
            segments_context = [
                {"speaker_label": seg.speaker_label, "source_text": seg.source_text}
                for seg in diarized
            ]
            # Translate Chinese → target (Khmer) DIRECTLY — no English pivot.
            logger.info(f"Translating {len(source_texts)} segments {project.source_lang} → {project.target_lang}...")
            km_texts, translation_report = await translate_batch_with_report(
                source_texts, project.source_lang, project.target_lang, segments_context=segments_context,
            )

            # Atomic swap: speakers, old-segment removal, new segments and the
            # COMPLETED status are all written in the one commit below.
            speaker_map = await ensure_speakers(db, project.id, [seg.speaker_label for seg in diarized])
            lane_by_speaker: dict = {}   # each speaker starts on its own lane (user can rearrange)
            new_segments = []
            for i, seg in enumerate(diarized):
                new_segments.append(Segment(
                    job_id=job.id,
                    speaker_id=speaker_map[seg.speaker_label].id,
                    lane_index=lane_by_speaker.setdefault(seg.speaker_label, len(lane_by_speaker)),
                    start_time=seg.start_time,
                    end_time=seg.end_time,
                    source_text=seg.source_text,
                    english_text="",
                    khmer_text=km_texts[i] if i < len(km_texts) else "",
                ))
            await replace_job_segments(db, job.id, new_segments)
            await remove_unused_auto_speakers(db, project.id)

            # used_mock: Gemini failed for some clips — their timing is real
            # (silence detection), only their text is missing. Still COMPLETED
            # (editable, re-runnable) but it must not look like a normal success.
            warnings = []
            if used_mock:
                warnings.append(
                    "Transcription was unavailable for some segments — their timing is correct "
                    "but the text is missing. Re-analyze to retry."
                )
            if translation_report.warning():
                warnings.append(translation_report.warning())
            await _update_job(db, job, JobStatus.COMPLETED, 100, " ".join(warnings))
            logger.info(
                f"Analysis complete for job {job_id[:8]} — {len(diarized)} segments, "
                f"{len(set(speaker_map[s.speaker_label].id for s in diarized))} speakers"
            )

        except Exception as e:
            logger.exception(f"Stage 2 failed for job {job_id[:8]}: {e}")
            await restore_or_fail_job(db, job_id, f"Analysis failed: {e}")


# ── Speaker detection on existing segments ────────────────────────────────────

async def run_speaker_detection(job_id: str) -> None:
    """
    Re-detect who speaks each EXISTING segment, keeping its text, timing,
    translation, edits and generated audio exactly as they are — only
    speaker_id changes. For sessions analyzed before speaker detection
    existed, or whose speakers came out wrong.
    """
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        job, project = await _load_job_and_project(db, job_id)
        if not project:
            return
        try:
            segments = (await db.execute(
                select(Segment).where(Segment.job_id == job_id).order_by(Segment.start_time)
            )).scalars().all()
            vocals_path = vocals_path_for(job)
            if not segments:
                raise RuntimeError("This session has no segments yet — run Analyze first.")
            if not vocals_path:
                raise RuntimeError("No extracted audio found for this session.")
            segment_ids = [s.id for s in segments]
            spans = [(s.start_time, s.end_time) for s in segments]

            taken_labels = await project_speaker_labels(db, project.id)
            with tempfile.TemporaryDirectory(prefix="speaker_refs_") as ref_dir:
                references = await build_speaker_references(db, project.id, ref_dir)
                await db.commit()   # release the connection before the Gemini calls
                # Analyse the audio fresh (whole scenes, accurate turns) and map
                # it onto the existing segments by overlap — see labels_for_spans.
                pieces, used_mock = await transcribe_audio(
                    vocals_path,
                    references=references,
                    taken_labels=taken_labels,
                    language=project.source_lang or "zh",
                )
            labels = labels_for_spans(spans, pieces)
            if labels is None:
                raise RuntimeError("Gemini didn't return any speech for this audio — try again in a minute.")

            speaker_map = await ensure_speakers(db, project.id, labels)
            # Re-read: the user may have deleted segments while Gemini was listening.
            current = {s.id: s for s in (await db.execute(
                select(Segment).where(Segment.id.in_(segment_ids))
            )).scalars().all()}
            for segment_id, label in zip(segment_ids, labels):
                if segment_id in current:
                    current[segment_id].speaker_id = speaker_map[label].id
            await db.flush()
            await remove_unused_auto_speakers(db, project.id)

            warning = (
                "Part of the audio couldn't be analysed — some segments took the nearest "
                "detected voice. Run Detect speakers again to retry."
                if used_mock else ""
            )
            await _update_job(db, job, JobStatus.COMPLETED, 100, warning)
            logger.info(f"Speaker detection done for job {job_id[:8]} — {len(set(labels))} speakers")

        except Exception as e:
            logger.exception(f"Speaker detection failed for job {job_id[:8]}: {e}")
            await restore_or_fail_job(db, job_id, f"Speaker detection failed: {e}")
