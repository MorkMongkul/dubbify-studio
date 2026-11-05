"""The single-voice dub analysis of one episode: after Stage 1 (separation, shared as it is), transcription on this Mac
(local_asr; its speaker labels are ignored), line timing, every line translated (single_voice.translate), and the lines
saved without speakers. No speaker grouping, recognition, "Who is this?" or casting. Gemini hears the video only when
the user pressed Analyze (allow_gemini): otherwise an episode the Mac can't transcribe waits with a message. In a
fully automatic project the episode then goes on to its voices and export (single_voice.voices)."""
import logging

from app.core.config import settings  # noqa: F401 — the UPLOAD_DIR the shared helpers read
from app.models.models import JobStatus, Segment
from app.services import line_timing, local_asr
from app.services.asr import transcribe_audio
from app.services.pipeline_common import (
    update_job_status as _update_job, original_sound_for, replace_job_segments, restore_or_fail_job, translation_story, vocals_path_for,
)
from app.services.single_voice import DEFAULT_VOICE, MAX_SECS
from app.services.single_voice import translate as sv_translate
from app.services.timeline import lay_out_new

logger = logging.getLogger(__name__)



async def voice_and_export(job_id: str, export_path: str | None) -> None:
    from app.services.single_voice import voices
    await voices.voice_and_export(job_id, export_path)


async def run_analysis(job_id: str, allow_gemini: bool = False) -> None:
    """Transcribe, translate and save the lines of a single-voice episode (the job was claimed by the caller)."""
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.models import Job, Project

    async with AsyncSessionLocal() as db:
        job = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
        project = (await db.execute(select(Project).where(Project.id == job.project_id))).scalar_one_or_none() \
            if job else None
        if not project:
            return
        try:
            vocals = vocals_path_for(job)
            if not vocals:
                raise RuntimeError("No extracted audio found for this session — upload the video again.")
            language = project.source_lang or "zh"
            await _update_job(db, job, JobStatus.DIARIZING, 35)
            names = local_asr.names_from_notes(project.story_notes)
            clip_audio = original_sound_for(job)
            await db.commit()   # no connection held while transcribing
            lines, why = None, ("its model is still downloading, or this Mac or language can't use it")
            if local_asr.use_mac(language):
                try:
                    lines = await local_asr.transcribe(vocals, clip_audio or vocals, names, [])
                except Exception as e:   # noqa: BLE001 — out of memory, a model file gone: Gemini, on a click
                    logger.warning(f"Single-voice: transcribing on this Mac failed: {e}")
                    why = f"transcribing on this Mac failed: {e}"
            if lines is None and allow_gemini:
                lines, _ = await transcribe_audio(vocals, references=[], taken_labels=[], language=language,
                                                  clip_audio=clip_audio)
            if lines is None:
                await _update_job(db, job, JobStatus.STEMS_READY, 30,
                                  f"This episode wasn't transcribed ({why}). Press Analyze to transcribe it with "
                                  "Gemini — that sends its sound to Google and uses your Gemini credit.")
                return
            lines = [line for line in lines if (line.source_text or "").strip()]

            await _update_job(db, job, JobStatus.TRANSCRIBING, 55)
            await db.commit()
            await line_timing.refine(vocals, lines, language)

            story = await translation_story(db, project.id, job.id)
            memory = await sv_translate.memory(db, project.id, job.id)
            await _update_job(db, job, JobStatus.TRANSLATING, 65)
            khmer, report = await sv_translate.translate(
                [line.source_text for line in lines], [line.end_time - line.start_time for line in lines],
                story, memory, project.voice_name or DEFAULT_VOICE)

            segments = [Segment(job_id=job.id, speaker_id=None, lane_index=0, start_time=line.start_time,
                                end_time=line.end_time, source_start=line.start_time, source_end=line.end_time,
                                source_text=line.source_text, english_text="", khmer_text=km, khmer_machine=km)
                        for line, km in zip(lines, khmer)]
            lay_out_new(segments)   # each line as long as its Khmer takes to say, on its own track when it overlaps
            await replace_job_segments(db, job.id, segments)

            warnings = []
            if (job.duration_secs or 0) > MAX_SECS:
                warnings.append("This video is over 40 minutes: single-voice dub is made for videos up to about 20 "
                                "minutes, and a longer one may run out of memory on an 8 GB Mac.")
            if report.warning():
                warnings.append(report.warning())
            if job.duck_music is None:
                job.duck_music = True
            await _update_job(db, job, JobStatus.COMPLETED, 100, " ".join(warnings))
            logger.info(f"Single-voice analysis of job {job_id[:8]}: {len(segments)} lines")
        except Exception as e:   # noqa: BLE001 — the old lines stay (restore_or_fail_job)
            logger.exception(f"Single-voice analysis failed for job {job_id[:8]}: {e}")
            await restore_or_fail_job(db, job_id, f"Analysis failed: {e}")
            return
        auto = bool(project.auto_export) and not report.failed
        if auto:
            # Claimed like the Generate voices & export button: an Export click meanwhile gets "busy"
            from sqlalchemy import update
            claimed = await db.execute(update(Job).where(Job.id == job_id, Job.status == JobStatus.COMPLETED)
                                       .values(status=JobStatus.SYNTHESIZING, progress=5))
            await db.commit()
            auto = claimed.rowcount == 1

    if auto:
        await voice_and_export(job_id, None)
