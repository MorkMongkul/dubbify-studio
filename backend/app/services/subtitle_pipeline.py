"""
app/services/subtitle_pipeline.py
Subtitle-based pipeline — two user-controlled stages.

Stage 1  run_subtitle_pipeline()          → parse subtitles + extract + separate → STEMS_READY
Stage 2  run_subtitle_analysis_pipeline() → speaker-label + translate           → COMPLETED

Skips ASR entirely — subtitles are always more accurate than ASR. Speaker
labels come from app.services.asr.label_speech, sliced at each subtitle
line's own timing (see its docstring on how this works and its accuracy
caveats) — subtitle TEXT is always kept as-is from the file; only speaker
labels come from Gemini.
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
from app.services.subtitle_parser import parse_subtitle_file
from app.services.asr import DEFAULT_SPEAKER, label_speech
from app.services.diarizer import DiarizedSegment
from app.services.translator import translate_batch_with_report
from app.services.pipeline_common import (
    update_job_status as _update_job,
    build_speaker_references,
    ensure_speakers,
    project_speaker_labels,
    remove_unused_auto_speakers,
    restore_or_fail_job,
    original_sound_for,
    translation_cast,
    translation_memory,
    translation_story,
    vocals_path_for,
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
            vocals_path, _bgm, degraded = await separate_vocals_bgm(audio_path, str(job_dir), job.duration_secs)
            logger.info(f"Separation complete — vocals: {vocals_path}")

            # The extracted full-mix WAV (~10MB/min) is only needed as a
            # diarization fallback for jobs where separation produced nothing —
            # once real stems exist, reclaim it.
            if vocals_path != audio_path and Path(vocals_path).exists():
                Path(audio_path).unlink(missing_ok=True)

            # Store subtitle text as Segments without speakers so the
            # user can see the script while the stems play on the timeline.
            # Speakers are assigned in Stage 2 after diarization.
            warning = (
                "Vocal/music separation was unavailable — using the original mixed "
                "audio for both the Vocals and BGM tracks. Speaker detection accuracy "
                "may be reduced, and BGM will contain some dialogue."
                if degraded else ""
            )
            await _update_job(db, job, JobStatus.STEMS_READY, 30, warning)
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

            if degraded:
                logger.warning(f"Job {job_id[:8]} is STEMS_READY with DEGRADED (unseparated) stems")
            else:
                logger.info(f"Job {job_id[:8]} STEMS_READY — {len(subtitle_segments)} subtitle lines saved, waiting for Analyze")

        except Exception as e:
            logger.exception(f"Subtitle Stage 1 failed for job {job_id[:8]}: {e}")
            await _update_job(db, job, JobStatus.FAILED, 0, str(e))


# ── STAGE 2: Speaker-label + translate ──────────────────────────────────────────

async def run_subtitle_analysis_pipeline(job_id: str, max_speakers: int | None = None) -> None:
    """
    Stage 2 for subtitle jobs: label speakers for every existing Segment
    (audio-based, at each line's own subtitle timing), then translate.
    Triggered by user clicking Analyze.

    Subtitle text/timing is never touched — only speaker_id changes here.
    max_speakers is accepted for API compatibility, ignored (see
    app.services.asr — Gemini decides how many voices it hears).
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

        try:
            vocals_path = vocals_path_for(job)
            if not vocals_path:
                raise RuntimeError("No extracted audio found for this session — upload the video again.")
            await _update_job(db, job, JobStatus.DIARIZING, 35)

            seg_result = await db.execute(
                select(Segment).where(Segment.job_id == job_id).order_by(Segment.start_time)
            )
            existing_segments = seg_result.scalars().all()
            spans = [(s.start_time, s.end_time) for s in existing_segments]

            taken_labels = await project_speaker_labels(db, project.id)
            with tempfile.TemporaryDirectory(prefix="speaker_refs_") as ref_dir:
                references = await build_speaker_references(db, project.id, ref_dir, exclude_job_id=job.id)
                # Release the connection before the Gemini calls — the dev SQLite
                # engine has exactly one, and every UI poll needs it meanwhile.
                await db.commit()
                labels, _texts, used_mock = await label_speech(
                    vocals_path, spans,
                    references=references,
                    taken_labels=taken_labels,
                    language=project.source_lang or "zh",
                    clip_audio=original_sound_for(job),
                )
            if not labels:
                labels = [DEFAULT_SPEAKER] * len(existing_segments)

            # Subtitle text/timing is kept as-is; only the speaker assignment changes.
            speaker_map = await ensure_speakers(db, project.id, labels)

            await _update_job(db, job, JobStatus.TRANSCRIBING, 50)

            lane_by_speaker: dict = {}
            for db_seg, label in zip(existing_segments, labels):
                speaker = speaker_map.get(label)
                db_seg.speaker_id = speaker.id if speaker else None
                db_seg.lane_index = lane_by_speaker.setdefault(label, len(lane_by_speaker))
            await db.flush()
            await remove_unused_auto_speakers(db, project.id)
            await db.commit()

            # Translate
            story = await translation_story(db, project.id, job.id)
            cast = await translation_cast(db, job, labels)
            memory = await translation_memory(db, project.id, job.id)
            await _update_job(db, job, JobStatus.TRANSLATING, 65)
            source_texts = [s.source_text for s in existing_segments]
            segments_context = [
                {"speaker_label": label, "source_text": s.source_text}
                for s, label in zip(existing_segments, labels)
            ]

            # Translate source → target (Khmer) DIRECTLY — no English pivot.
            km_texts, translation_report = await translate_batch_with_report(
                source_texts, project.source_lang, project.target_lang, segments_context=segments_context,
                story=story, cast=cast, memory=memory,
            )

            await _update_job(db, job, JobStatus.TRANSLATING, 85)
            kept = 0
            for i, db_seg in enumerate(existing_segments):
                new_text = km_texts[i] if i < len(km_texts) else ""
                old_text = db_seg.khmer_text or ""
                if not new_text and old_text:
                    # Nothing came back (translation failed, or the line has
                    # no source text): keep the translation and voice the
                    # line already has rather than emptying it.
                    kept += bool((db_seg.source_text or "").strip())
                    continue
                if new_text != old_text:
                    # The old voice clip speaks the old words — the line goes
                    # back to Pending instead of exporting a mismatched voice.
                    db_seg.tts_audio_path = ""
                    db_seg.tts_duration_secs = 0.0
                db_seg.english_text = ""
                db_seg.khmer_text   = new_text
            await db.commit()
            # Only lines that had no translation before are actually left empty.
            translation_report.failed = max(0, translation_report.failed - kept)

            # used_mock means speaker labeling failed for at least one batch
            # — subtitle text/timing is unaffected either way (it came from
            # the file, not audio), only some speaker assignments defaulted.
            warnings = []
            if used_mock:
                warnings.append(
                    "Speaker labeling was unavailable for some segments — they default to "
                    "the neighbouring voice. Subtitle text/timing is still correct. Run Detect speakers to retry."
                )
            if kept:
                warnings.append(
                    f"{kept} line(s) couldn't be translated again and kept their previous translation."
                )
            if translation_report.warning():
                warnings.append(translation_report.warning())
            await _update_job(db, job, JobStatus.COMPLETED, 100, " ".join(warnings))
            logger.info(f"Subtitle analysis complete for job {job_id[:8]} — {len(existing_segments)} segments.")

        except Exception as e:
            logger.exception(f"Subtitle Stage 2 failed for job {job_id[:8]}: {e}")
            await restore_or_fail_job(db, job_id, f"Analysis failed: {e}")
