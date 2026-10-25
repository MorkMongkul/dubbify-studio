"""
app/services/pipeline.py
ASR pipeline orchestrator — two user-controlled stages.

Stage 1  run_pipeline()           → extract audio + stem separation (HF Space) → STEMS_READY
Stage 2  run_analysis_pipeline()  → transcribe + recognise characters + translate  → COMPLETED

Speaker labels come from app.services.asr (Gemini comparing voices across
clips in one request) rather than a dedicated diarization model like MOSS's
— treat them as an approximate starting point, not ground truth. Once a
project has characters, each line's voice and speaking face are matched to
them first (app.services.characters.store.recognise); Gemini's labels then
only name people not seen before. The user reassigns anything wrong.

TTS synthesis and final mix are triggered separately per-segment / on demand.
"""
import logging
import tempfile
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.models.models import Job, Project, Segment, Speaker, JobStatus
from app.services.audio_extractor import extract_audio, get_video_duration, video_frame_rate
from app.services.source_separator import render_bgm_volume, separate_vocals_bgm
from app.services.asr import VOICES, labels_for_spans, transcribe_audio
from app.services.translator import EARLIER_LINES, translate_batch_with_report
from app.services.timeline import actor_span, follow_text, lay_out_new, settle_job
from app.services.pipeline_common import (
    update_job_status as _update_job,
    build_speaker_references,
    ensure_speakers,
    project_speaker_labels,
    remove_unused_auto_speakers,
    replace_job_segments,
    original_sound_for,
    restore_or_fail_job,
    translation_cast,
    translation_memory,
    translation_story,
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
            job.frame_rate = await video_frame_rate(job.video_path)

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


async def run_separation_retry(job_id: str, return_status: JobStatus) -> None:
    """
    "Try again" after separation fell back to the original audio: extract the
    audio again and re-run separation. Either way the job returns to
    `return_status` (completed when it has lines, stems_ready otherwise); its
    lines, translations and voices are untouched.
    """
    from app.core.database import AsyncSessionLocal
    from app.core.paths import resolve_media_path

    async with AsyncSessionLocal() as db:
        job = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
        if not job:
            logger.error(f"Job {job_id} not found.")
            return
        job_dir = Path(settings.UPLOAD_DIR) / job.project_id / job_id
        progress = 100 if return_status == JobStatus.COMPLETED else 30
        try:
            audio_path = await extract_audio(resolve_media_path(job.video_path), output_dir=str(job_dir))
            job.audio_path = audio_path
            await db.commit()
            _, _, degraded = await separate_vocals_bgm(audio_path, str(job_dir), job.duration_secs, force=True)
            if not degraded:
                Path(audio_path).unlink(missing_ok=True)
                if job.bgm_volume_db:
                    # Keep the music level the user chose, now on the new stem
                    await render_bgm_volume(job_dir, job.bgm_volume_db)
            message = (
                "Music and voices still couldn't be separated, so the music track keeps the "
                "original voices. Try again later." if degraded else ""
            )
            await _update_job(db, job, return_status, progress, message)
        except Exception as e:
            logger.exception(f"Separation retry failed for job {job_id[:8]}: {e}")
            await _update_job(db, job, return_status, progress, f"Separating music and voices failed: {e}")


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
    from app.services.characters import store   # here, so the backend starts without the characters package

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
                references = await build_speaker_references(db, project.id, ref_dir, exclude_job_id=job.id)
                # Release the connection before minutes of Gemini calls — the dev
                # SQLite engine has exactly one.
                await db.commit()
                diarized, used_mock = await transcribe_audio(
                    vocals_path,
                    references=references,
                    taken_labels=taken_labels,
                    language=project.source_lang or "zh",
                    clip_audio=original_sound_for(job),
                )

            await _update_job(db, job, JobStatus.TRANSCRIBING, 55)
            has_text = sum(1 for s in diarized if s.source_text)
            logger.info(f"Transcription: {has_text}/{len(diarized)} segments have text")

            # Who speaks each line, before translating: Khmer politeness and pronouns depend on it
            await _update_job(db, job, JobStatus.RECOGNIZING, 60)
            spans = [(seg.start_time, seg.end_time) for seg in diarized]
            gemini_labels = [seg.speaker_label for seg in diarized]
            kept = await store.kept_characters(db, job, spans)
            labels, who_checks, prints = await store.recognise(
                db, job, spans, gemini_labels, kept, refresh_kept=lambda: store.kept_characters(db, job, spans))

            story = await translation_story(db, project.id, job.id)
            heard = store.heard_by_label(labels, gemini_labels, who_checks, kept,
                                         [seg.gender if seg.gender in VOICES else None for seg in diarized])
            cast = await translation_cast(db, job, labels, heard)
            memory = await translation_memory(db, project.id, job.id)
            await _update_job(db, job, JobStatus.TRANSLATING, 65)
            source_texts = [seg.source_text for seg in diarized]
            segments_context = [
                {"speaker_label": label, "source_text": seg.source_text}
                for label, seg in zip(labels, diarized)
            ]
            # Translate Chinese → target (Khmer) DIRECTLY — no English pivot.
            logger.info(f"Translating {len(source_texts)} segments {project.source_lang} → {project.target_lang}...")
            km_texts, translation_report = await translate_batch_with_report(
                source_texts, project.source_lang, project.target_lang, segments_context=segments_context,
                story=story, cast=cast, memory=memory,
            )

            # Atomic swap: speakers, old-segment removal, new segments and the
            # COMPLETED status are all written in the one commit below.
            speaker_map = await ensure_speakers(db, project.id, labels, heard)
            lane_by_speaker: dict = {}   # each speaker starts on its own lane (user can rearrange)
            new_segments = []
            for i, seg in enumerate(diarized):
                km = km_texts[i] if i < len(km_texts) else ""
                new_segments.append(Segment(
                    job_id=job.id,
                    speaker_id=speaker_map[labels[i]].id,
                    lane_index=lane_by_speaker.setdefault(labels[i], len(lane_by_speaker)),
                    start_time=seg.start_time,
                    end_time=seg.end_time,
                    source_start=seg.start_time,
                    source_end=seg.end_time,
                    source_text=seg.source_text,
                    english_text="",
                    khmer_text=km,
                    khmer_machine=km,
                    who_check=store.who_check_json(who_checks[i]),
                    who_confirmed=bool(kept[i]),   # a confirmed old line's character stays confirmed
                ))
            # Each line is as long as its Khmer text takes to say; the
            # detected span stays as where the actor spoke
            lay_out_new(new_segments)
            await replace_job_segments(db, job.id, new_segments)
            await store.save_prints(db, job.id, [s.id for s in new_segments], prints, store.faces_dir(job))
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
            if job.duck_music is None:
                job.duck_music = True   # analysed from 0.3.1 on: the music dips under the voices
            await _update_job(db, job, JobStatus.COMPLETED, 100, " ".join(warnings))
            logger.info(
                f"Analysis complete for job {job_id[:8]} — {len(diarized)} segments, "
                f"{len(set(speaker_map[label].id for label in labels))} speakers"
            )

        except Exception as e:
            logger.exception(f"Stage 2 failed for job {job_id[:8]}: {e}")
            await restore_or_fail_job(db, job_id, f"Analysis failed: {e}")


# ── Translate missing lines ───────────────────────────────────────────────────

def needs_translation(segment: Segment) -> bool:
    """Has words to translate but no translation yet."""
    return bool((segment.source_text or "").strip()) and not (segment.khmer_text or "").strip()


async def run_translation(job_id: str) -> None:
    """
    Translate the lines that have source text but no translation — the way back
    from a translation outage, without re-running transcription. Lines that
    already have a translation, including the user's own edits, are never
    touched (also not one typed while this runs).
    """
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        job, project = await _load_job_and_project(db, job_id)
        if not project:
            return
        try:
            rows = (await db.execute(
                select(Segment, Speaker.label).outerjoin(Speaker, Speaker.id == Segment.speaker_id)
                .where(Segment.job_id == job_id).order_by(Segment.start_time)
            )).all()
            todo = [(seg.id, seg.source_text, label or "SPEAKER") for seg, label in rows if needs_translation(seg)]
            if todo:
                story = await translation_story(db, project.id, job.id)
                cast = await translation_cast(db, job, [label for seg, label in rows if needs_translation(seg)])
                memory = await translation_memory(db, project.id, job.id)
                await db.commit()   # release the connection before the Gemini calls
                logger.info(f"Translating {len(todo)} missing line(s) for job {job_id[:8]}")
                texts, report = await translate_batch_with_report(
                    [text for _, text, _ in todo], project.source_lang, project.target_lang,
                    segments_context=[{"speaker_label": label, "source_text": text} for _, text, label in todo],
                    story=story, cast=cast, memory=memory,
                )
                # Fresh from the database (not this session's earlier copies): a
                # translation the user typed meanwhile must win.
                current = {s.id: s for s in (await db.execute(
                    select(Segment).where(Segment.id.in_([seg_id for seg_id, _, _ in todo]))
                    .execution_options(populate_existing=True)
                )).scalars().all()}
                filled = []
                for (seg_id, _, _), text in zip(todo, texts):
                    seg = current.get(seg_id)
                    if seg is not None and text and needs_translation(seg):
                        seg.khmer_text = seg.khmer_machine = text
                        follow_text(seg)    # a line with no voice is as long as its text
                        filled.append(seg_id)
                await db.commit()
                await settle_job(db, job.id, filled)
                warning = report.warning()
            else:
                warning = ""
            await _update_job(db, job, JobStatus.COMPLETED, 100, warning)
        except Exception as e:
            logger.exception(f"Translating missing lines failed for job {job_id[:8]}: {e}")
            await restore_or_fail_job(db, job_id, f"Translation failed: {e}")


def is_machine_khmer(segment: Segment) -> bool:
    """The Khmer is still what translation wrote: nobody edited it. Lines from before 0.5.0 (no
    khmer_machine) count as edited."""
    return segment.khmer_machine is not None and segment.khmer_text == segment.khmer_machine


async def retranslate_line(db: AsyncSession, segment_id: str) -> bool:
    """
    Translate one line again for the speaker it now has (picking a character): one Gemini call
    with the same Story notes, WHO IS SPEAKING cast and memory as the episode's translation, and
    the lines before it with their Khmer. Only an unedited line, only by Gemini: the free fallback
    ignores who is speaking and would replace better Khmer. Returns whether the Khmer changed;
    never raises — the pick has been saved already and must stand.
    """
    try:
        seg = (await db.execute(select(Segment).where(Segment.id == segment_id)
                                .execution_options(populate_existing=True))).scalar_one_or_none()
        if seg is None or not is_machine_khmer(seg) or not (seg.source_text or "").strip():
            return False
        if settings.TRANSLATION_BACKEND.lower() != "gemini" or not settings.GEMINI_API_KEY:
            return False
        job, project = await _load_job_and_project(db, seg.job_id)
        if not project:
            return False
        label = (await db.execute(select(Speaker.label).where(Speaker.id == seg.speaker_id))).scalar_one_or_none() \
            if seg.speaker_id else None
        label = label or "SPEAKER"
        machine, source = seg.khmer_machine, seg.source_text
        before = (await db.execute(
            select(Segment.source_text, Segment.khmer_text, Speaker.label)
            .outerjoin(Speaker, Speaker.id == Segment.speaker_id)
            .where(Segment.job_id == seg.job_id, Segment.id != seg.id, Segment.start_time <= seg.start_time)
            .order_by(Segment.start_time.desc()).limit(EARLIER_LINES * 2)
        )).all()
        earlier = [{"speaker_label": lab or "SPEAKER", "source_text": zh, "translation": km}
                   for zh, km, lab in reversed(before) if (zh or "").strip() and (km or "").strip()][-EARLIER_LINES:]
        story = await translation_story(db, project.id, job.id)
        cast = await translation_cast(db, job, [label] + [e["speaker_label"] for e in earlier])
        memory = await translation_memory(db, project.id, job.id)
        await db.commit()   # release the connection before the Gemini call

        texts, report = await translate_batch_with_report(
            [source], project.source_lang, project.target_lang,
            segments_context=[{"speaker_label": label, "source_text": source}],
            story=story, cast=cast, memory=memory, earlier=earlier,
        )
        text = texts[0] if texts else ""
        if not text or report.fallback or report.failed:
            return False   # the free fallback knows nothing of who speaks; keep Gemini's earlier Khmer

        # Fresh from the database (not this session's earlier copy): a Khmer edit made meanwhile wins
        seg = (await db.execute(select(Segment).where(Segment.id == segment_id)
                                .execution_options(populate_existing=True))).scalar_one_or_none()
        if seg is None or seg.khmer_text != machine or seg.khmer_machine != machine:
            return False
        seg.khmer_text = seg.khmer_machine = text
        follow_text(seg)    # a line with no voice is as long as its text
        await db.commit()
        await settle_job(db, seg.job_id, [seg.id])
        return True
    except Exception as e:
        logger.warning(f"Re-translating line {segment_id[:8]} for its new speaker failed: {e}")
        await db.rollback()
        return False


# ── Speaker detection on existing segments ────────────────────────────────────

async def run_speaker_detection(job_id: str) -> None:
    """
    Re-detect who speaks each EXISTING segment, keeping its text, timing,
    translation, edits and generated audio exactly as they are — only
    speaker_id changes. For sessions analyzed before speaker detection
    existed, or whose speakers came out wrong.
    """
    from app.core.database import AsyncSessionLocal
    from app.services.characters import store

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
            # Characters first: lines are checked against the project's characters, locally, no Gemini
            outcome = await store.identify(db, job, segments)
            if outcome == "done":
                await remove_unused_auto_speakers(db, project.id)
                await _update_job(db, job, JobStatus.COMPLETED, 100, "")
                logger.info(f"Speaker detection done for job {job_id[:8]} — checked against the characters")
                return
            if not settings.GEMINI_API_KEY and outcome != "no_profiles":   # the fallback below needs it
                raise RuntimeError(
                    "This episode's voices weren't separated, so Movelo can't recognise them, and no Gemini key is "
                    "set — add one in Settings → AI models." if outcome == "no_vocals" else
                    "Movelo can't recognise voices on this Mac and no Gemini key is set — add one in Settings → "
                    "AI models.")

            # Lines the user confirmed are never relabelled
            segments = [s for s in segments if not store.is_confirmed(s, job)]
            segment_ids = [s.id for s in segments]
            # Where the actor spoke, not where the (perhaps moved) clip is
            spans = [actor_span(s) for s in segments]
            if not segments:
                await _update_job(db, job, JobStatus.COMPLETED, 100, "")
                return

            taken_labels = await project_speaker_labels(db, project.id)
            with tempfile.TemporaryDirectory(prefix="speaker_refs_") as ref_dir:
                references = await build_speaker_references(db, project.id, ref_dir, exclude_job_id=job.id)
                await db.commit()   # release the connection before the Gemini calls
                # Analyse the audio fresh (whole scenes, accurate turns) and map
                # it onto the existing segments by overlap — see labels_for_spans.
                pieces, used_mock = await transcribe_audio(
                    vocals_path,
                    references=references,
                    taken_labels=taken_labels,
                    language=project.source_lang or "zh",
                    clip_audio=original_sound_for(job),
                )
            labels = labels_for_spans(spans, pieces)
            if labels is None:
                raise RuntimeError("Gemini didn't return any speech for this audio — try again in a minute.")

            heard = {p.speaker_label: p.gender for p in pieces if p.gender in VOICES}
            speaker_map = await ensure_speakers(db, project.id, labels, heard)
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
