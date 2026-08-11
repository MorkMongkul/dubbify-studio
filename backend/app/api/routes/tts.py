"""
app/api/routes/tts.py
TTS synthesis endpoints — trigger VoxCPM2 for individual segments or entire jobs.
"""
import asyncio
import logging
import shutil
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from pydantic import BaseModel
 
from app.core.database import get_db
from app.core.config import settings
from app.models.models import Segment, Speaker, Job, JobStatus, Voice, VoiceMode, Overlay
from app.schemas.schemas import TTSResponse
from app.services.tts_client import tts_client
from app.services.audio_extractor import mix_dubbed_audio, probe_video
from app.services.video_overlay import render_subtitle_pngs
from app.core.paths import resolve_media_path, resolve_existing

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tts", tags=["TTS Synthesis"])


async def _resolve_voice(seg: Segment, db: AsyncSession) -> dict:
    """
    Resolve the synthesis parameters for a segment, in priority order:
      1. segment.voice_id        (per-segment override)
      2. speaker.voice_id        (speaker-level assignment)
      3. speaker.voice_design_prompt  (legacy fallback, design-only)

    Returns kwargs for tts_client.synthesize().
    """
    async def _voice_kwargs(voice: Voice) -> dict:
        # Reference-driven: if the voice has a reference clip (uploaded for
        # clone/ultimate, OR auto-baked from a design profile), clone from it so
        # the timbre is identical across every line. A transcript → ultimate
        # cloning; otherwise controllable cloning with the description as style.
        has_ref = bool(voice.reference_audio_path)
        has_transcript = has_ref and bool(voice.reference_transcript)
        return {
            # In ultimate cloning the style prompt is ignored, so drop it there.
            "voice_design": "" if has_transcript else voice.description,
            "cfg_value": voice.cfg_value,
            "inference_timesteps": voice.inference_timesteps,
            "reference_audio_path": voice.reference_audio_path or "",
            "reference_transcript": voice.reference_transcript if has_transcript else "",
            "seed": voice.seed,  # fixed seed → consistent voice across all lines
        }

    # 1. Per-segment override
    if seg.voice_id:
        v = (await db.execute(select(Voice).where(Voice.id == seg.voice_id))).scalar_one_or_none()
        if v:
            return await _voice_kwargs(v)

    # 2 & 3. Speaker-level voice, then legacy design prompt
    if seg.speaker_id:
        speaker = (await db.execute(select(Speaker).where(Speaker.id == seg.speaker_id))).scalar_one_or_none()
        if speaker:
            if speaker.voice_id:
                v = (await db.execute(select(Voice).where(Voice.id == speaker.voice_id))).scalar_one_or_none()
                if v:
                    return await _voice_kwargs(v)
            return {"voice_design": speaker.voice_design_prompt or ""}

    return {"voice_design": ""}
 
 
async def _synthesize_segment_db(segment_id: str, db: AsyncSession) -> Segment:
    result = await db.execute(select(Segment).where(Segment.id == segment_id))
    seg = result.scalar_one_or_none()
    if not seg:
        raise HTTPException(status_code=404, detail="Segment not found")

    if not seg.khmer_text or not seg.khmer_text.strip():
        raise HTTPException(status_code=400, detail="Segment has no Khmer text to synthesize")

    # Resolve the assigned voice (segment override → speaker voice → legacy prompt)
    voice_kwargs = await _resolve_voice(seg, db)

    # Resolve project_id for correct upload path
    j_result = await db.execute(select(Job).where(Job.id == seg.job_id))
    job = j_result.scalar_one_or_none()
    project_id = job.project_id if job else seg.job_id

    # Build output path: uploads/{project_id}/{job_id}/tts/seg_{id}.wav
    job_dir = Path(settings.UPLOAD_DIR) / project_id / seg.job_id
    tts_dir = job_dir / "tts"
    tts_dir.mkdir(parents=True, exist_ok=True)
    output_raw_path = tts_dir / f"seg_{segment_id}_raw.wav"
    output_final_path = tts_dir / f"seg_{segment_id}.wav"

    # Release the DB connection before the TTS call — synthesis can take
    # minutes (Modal cold start), and the dev SQLite engine has exactly one
    # pooled connection, so holding it here would block every other request
    # (job polls, segment fetches) for the whole call. expire_on_commit=False
    # keeps `seg`/ORM objects usable after the commit.
    await db.commit()

    # Call VoxCPM2 or Gemini TTS
    result_data = await tts_client.synthesize(
        text=seg.khmer_text,
        output_path=str(output_raw_path),
        **voice_kwargs,
    )

    # Mock (silent) audio at the end of the fallback chain means every real
    # backend failed or rate-limited. When real backends ARE configured, treat
    # that as a failure so the segment stays visibly ungenerated and can be
    # retried — a silent clip saved as "done" is far worse than no clip.
    # (With zero keys configured — pure dev mode — mock is accepted as-is.)
    if result_data.get("mock") and (settings.VOXCPM2_API_URL or settings.GEMINI_API_KEY):
        raise HTTPException(
            status_code=503,
            detail="All TTS backends failed or rate-limited — wait a minute and retry",
        )

    if result_data["success"]:
        from app.services.audio_extractor import apply_audio_effects
        new_duration = await apply_audio_effects(
            input_path=str(output_raw_path),
            output_path=str(output_final_path),
            volume_db=seg.volume_db,
            voice_filter=seg.voice_filter,
            voice_speed=seg.voice_speed,
        )
        # The raw pre-effects clip is KEPT: segments.update_segment re-applies
        # effects from it whenever the user changes volume/filter/speed. Deleting
        # it here made that path fall back to copying the already-processed final
        # file, so every later tweak stacked on top of the baked-in one (set
        # +6 dB, regenerate, then change to +3 dB and the clip came out +9 dB).
        seg.tts_audio_path    = str(output_final_path)
        seg.tts_duration_secs = new_duration
        await db.flush()
        return seg
    else:
        raise HTTPException(status_code=500, detail=result_data.get("error", "TTS synthesis failed"))
 
 
@router.post("/synthesize/segment/{segment_id}", response_model=TTSResponse)
async def synthesize_segment(
    segment_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Synthesize a single segment.
    """
    seg = await _synthesize_segment_db(segment_id, db)
    await db.commit()
    return TTSResponse(
        segment_id=segment_id,
        audio_path=seg.tts_audio_path,
        duration_secs=seg.tts_duration_secs,
        success=True,
        error="",
    )
 
 
class BatchSynthesizeRequest(BaseModel):
    segment_ids: List[str]
 
 
@router.post("/synthesize/batch", status_code=202)
async def synthesize_batch(
    payload: BatchSynthesizeRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Synthesize multiple segments in the background (sequentially, with a pacing
    delay to prevent Gemini API rate limit errors). Runs as a background task
    because a batch can take minutes — a synchronous response would outlive the
    client timeout, and the request would pin the sole SQLite connection.
    Poll the job's segments to watch clips complete.
    """
    result = await db.execute(
        select(Segment).where(Segment.id.in_(payload.segment_ids))
    )
    by_id = {s.id: s for s in result.scalars().all()}
    valid_ids = [
        sid for sid in payload.segment_ids
        if sid in by_id and by_id[sid].khmer_text and by_id[sid].khmer_text.strip()
    ]
    if not valid_ids:
        raise HTTPException(status_code=400, detail="No segments with text to synthesize")

    background_tasks.add_task(_synthesize_ids_task, valid_ids)
    return {
        "message": f"Synthesis started for {len(valid_ids)} segments.",
        "started": len(valid_ids),
        "segment_ids": valid_ids,
        "skipped": [sid for sid in payload.segment_ids if sid not in valid_ids],
    }


async def _synthesize_ids_task(segment_ids: List[str]) -> None:
    """Background task: synthesize the given segments one at a time.

    Commits after every segment so results appear incrementally to polling
    clients, and the DB connection is released during each TTS call.
    Segments that fail (rate limits, backend hiccups) get a second pass after
    a cool-off, so one bad stretch doesn't permanently skip half the batch.
    """
    from app.core.database import AsyncSessionLocal

    async def _try_one(db, segment_id: str) -> bool:
        try:
            await _synthesize_segment_db(segment_id, db)
            await db.commit()
            return True
        except HTTPException as e:
            logger.warning(f"Batch synthesis: segment {segment_id[:8]} failed: {e.detail}")
            await db.rollback()
        except Exception:
            logger.exception(f"Batch synthesis: segment {segment_id[:8]} failed")
            await db.rollback()
        return False

    async with AsyncSessionLocal() as db:
        failed: List[str] = []
        for i, segment_id in enumerate(segment_ids):
            if not await _try_one(db, segment_id):
                failed.append(segment_id)
            # Pacing delay between generations (except after the last one)
            if i < len(segment_ids) - 1:
                await asyncio.sleep(1.5)

        if failed:
            logger.info(
                f"Batch synthesis: retrying {len(failed)} failed segment(s) "
                "after a 30s cool-off (rate limits usually clear by then)..."
            )
            await asyncio.sleep(30)
            still_failed = []
            for i, segment_id in enumerate(failed):
                if not await _try_one(db, segment_id):
                    still_failed.append(segment_id)
                if i < len(failed) - 1:
                    await asyncio.sleep(5)  # gentler pacing on the retry pass
            failed = still_failed

    done = len(segment_ids) - len(failed)
    if failed:
        logger.warning(
            f"Batch synthesis finished: {done}/{len(segment_ids)} generated, "
            f"{len(failed)} failed even after retry: {[s[:8] for s in failed]}"
        )
    else:
        logger.info(f"Batch synthesis finished ({len(segment_ids)} segments).")


@router.post("/synthesize/job/{job_id}")
async def synthesize_job(
    job_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Synthesize ALL approved segments in a job (runs in background).
    Only processes segments where is_approved=True.
    """
    result = await db.execute(
        select(Segment)
        .where(Segment.job_id == job_id, Segment.is_approved == True)
        .order_by(Segment.start_time)
    )
    segments = result.scalars().all()

    if not segments:
        raise HTTPException(
            status_code=400,
            detail="No approved segments found. Approve segments first.",
        )

    background_tasks.add_task(_synthesize_all_segments, job_id)
    await db.commit()

    return {
        "message": f"TTS synthesis started for {len(segments)} approved segments.",
        "job_id": job_id,
        "segment_count": len(segments),
    }


class MixRequest(BaseModel):
    # Absolute destination path on the machine running the app (backend and UI
    # run on the same machine — dev server or Tauri sidecar). When set, the
    # finished mix is copied there; when omitted, the file only lives under
    # uploads/ and is reachable via the job's output_url.
    export_path: str | None = None


@router.post("/mix/{job_id}", status_code=202)
async def mix_final_audio(
    job_id: str,
    background_tasks: BackgroundTasks,
    mute_original: bool = True,
    payload: MixRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Mix all synthesized TTS segments back into the original video, compositing
    whatever overlay layers (dropped images, subtitle box) the user has placed
    on this job in the editor. Produces the final dubbed .mp4 file, optionally
    copied to a user-chosen export path.

    Runs as a background task — a full-movie ffmpeg mix takes far longer than
    any sane HTTP timeout. The job goes to `mixing` status; poll /jobs/{id}
    until it reaches `completed` (output_url set) or `failed` (error_msg set).
    """
    j_result = await db.execute(select(Job).where(Job.id == job_id))
    job = j_result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status == JobStatus.MIXING:
        raise HTTPException(status_code=409, detail="A mix is already running for this job.")

    count_result = await db.execute(
        select(Segment.id)
        .where(Segment.job_id == job_id, Segment.tts_audio_path != "")
        .limit(1)
    )
    if count_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=400, detail="No TTS audio found. Run synthesis first.")

    export_path = (payload.export_path or "").strip() if payload else ""
    if export_path:
        # Validate up front so a bad destination fails the request, not the
        # multi-minute background mix.
        dest = Path(export_path).expanduser()
        if not dest.is_absolute():
            raise HTTPException(status_code=400, detail="export_path must be an absolute path")
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise HTTPException(status_code=400, detail=f"Cannot create export folder: {e}")
        export_path = str(dest)

    job.status = JobStatus.MIXING
    job.progress = 5
    job.error_msg = ""
    await db.commit()

    background_tasks.add_task(_mix_job_task, job_id, mute_original, export_path or None)
    return {
        "success": True,
        "message": "Mixing started — poll the job status until it completes.",
        "job_id": job_id,
        "export_path": export_path or None,
    }


def _autofit_rate(seg: Segment) -> float:
    """Speed-up needed for a TTS clip to fit its segment window.

    Must stay in lockstep with the editor's auto-fit preview
    (VideoPlayer.startTTS): same manual-speed opt-out, same 1.05 overflow
    threshold, same 3.5× cap — otherwise the export sounds slower than
    what the user heard while editing. Manual voice_speed is already baked
    into the clip file via atempo, so those clips are exported as-is.
    """
    if seg.voice_speed is not None and abs(seg.voice_speed - 1.0) > 0.001:
        return 1.0
    window = max(0.1, seg.end_time - seg.start_time)
    tts_duration = seg.tts_duration_secs or 0.0
    if tts_duration <= 0:
        return 1.0
    fit_rate = tts_duration / window
    return min(3.5, fit_rate) if fit_rate > 1.05 else 1.0


async def _mix_job_task(job_id: str, mute_original: bool, export_path: str | None = None) -> None:
    """Background task: run the full ffmpeg mix + overlay composite."""
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        j_result = await db.execute(select(Job).where(Job.id == job_id))
        job = j_result.scalar_one_or_none()
        if not job:
            logger.error(f"Mix task: job {job_id} not found")
            return

        result = await db.execute(
            select(Segment)
            .where(Segment.job_id == job_id, Segment.tts_audio_path != "")
            .order_by(Segment.start_time)
        )
        segments = result.scalars().all()

        overlay_result = await db.execute(
            select(Overlay).where(Overlay.job_id == job_id).order_by(Overlay.z_index)
        )
        job_overlays = overlay_result.scalars().all()

        # Stored paths may be relative (older rows) — resolve them here so the
        # export works regardless of the process's working directory.
        tts_seg_list = [
            {
                "start_time": seg.start_time,
                "audio_path": resolve_media_path(seg.tts_audio_path),
                "duration": seg.tts_duration_secs,
                "fit_rate": _autofit_rate(seg),
            }
            for seg in segments
            if resolve_existing(seg.tts_audio_path)
        ]
        missing_clips = len(segments) - len(tts_seg_list)
        if missing_clips:
            logger.warning(f"Mix: {missing_clips} TTS clip(s) missing on disk — skipped")
        if not tts_seg_list:
            job.status = JobStatus.COMPLETED
            job.error_msg = "No synthesized audio files were found on disk. Re-generate the voices and try again."
            await db.commit()
            return

        job_dir = Path(settings.UPLOAD_DIR) / job.project_id / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(job_dir / "dubbed_output.mp4")
        source_video = resolve_media_path(job.video_path)
        if not Path(source_video).exists():
            job.status = JobStatus.COMPLETED
            job.error_msg = f"Source video not found on disk ({job.video_path}). It may have been moved or deleted."
            await db.commit()
            logger.error(f"Mix aborted for job {job_id[:8]}: source video missing at {source_video}")
            return

        # Use separated BGM track when available — gives clean BGM + TTS with no voice bleed-through
        bgm_wav = job_dir / "no_vocals.wav"

        try:
            # Built in z-index order (job_overlays is already queried that way) so
            # shapes/images/subtitle lines interleave correctly — e.g. a shape meant
            # to cover the original video's subtitle only reads right if it stays
            # positioned below the new subtitle text in this same ordered list, not
            # grouped separately by kind.
            video_layers: list = []
            video_width = video_height = None
            if job_overlays:
                probe = await probe_video(source_video)
                video_stream = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), None)
                video_width = int(video_stream["width"]) if video_stream else 1080
                video_height = int(video_stream["height"]) if video_stream else 1920

                for ov in job_overlays:
                    if ov.type == "image":
                        overlay_media = resolve_existing(ov.media_path)
                        if not overlay_media:
                            continue
                        video_layers.append({
                            "kind": "image",
                            "media_path": str(overlay_media),
                            "x": ov.x, "y": ov.y, "width": ov.width, "height": ov.height,
                            "opacity": ov.opacity,
                            "start_time": ov.start_time, "end_time": ov.end_time,
                        })
                    elif ov.type == "shape":
                        video_layers.append({
                            "kind": "shape",
                            "x": ov.x, "y": ov.y, "width": ov.width, "height": ov.height,
                            "color": ov.color, "opacity": ov.opacity, "blur": ov.blur,
                            "start_time": ov.start_time, "end_time": ov.end_time,
                        })
                    elif ov.type == "subtitle":
                        # Pillow text rendering is CPU-bound — keep it off the event loop
                        subtitle_layers = await asyncio.to_thread(
                            render_subtitle_pngs,
                            segments,
                            box={"x": ov.x, "y": ov.y, "width": ov.width, "height": ov.height},
                            video_width=video_width,
                            video_height=video_height,
                            out_dir=job_dir / "subs",
                            font_size=ov.font_size,
                            color=ov.color,
                            outline_color=ov.outline_color,
                            background_color=ov.background_color,
                        )
                        for layer in subtitle_layers:
                            layer["kind"] = "image"
                            layer["opacity"] = 1.0
                        video_layers.extend(subtitle_layers)

            # Release the DB connection for the duration of the (long) ffmpeg run
            await db.commit()

            final_path = await mix_dubbed_audio(
                video_path=source_video,
                tts_segments=tts_seg_list,
                output_path=output_path,
                mute_original=mute_original,
                bgm_path=str(bgm_wav) if bgm_wav.exists() else None,
                video_layers=video_layers or None,
                video_width=video_width,
                video_height=video_height,
            )
            job.output_path = final_path
            job.status = JobStatus.COMPLETED
            job.progress = 100
            # Clear any failure from a previous attempt — without this a job
            # that once failed to mix kept reporting that error forever, so a
            # perfectly good export still surfaced "Audio mixing failed".
            job.error_msg = ""

            # Copy to the user-chosen destination (same machine — the backend
            # is the local dev server or the Tauri sidecar). A failed copy is
            # NOT a failed mix: the file still exists under uploads/, so only
            # error_msg carries the problem for the UI to surface.
            if export_path:
                try:
                    dest = Path(export_path)
                    if dest.is_dir():
                        dest = dest / Path(final_path).name
                    await asyncio.to_thread(shutil.copy2, final_path, dest)
                    logger.info(f"Export copied to {dest}")
                except OSError as e:
                    logger.exception(f"Export copy to {export_path} failed")
                    job.error_msg = (
                        f"Video compiled, but saving to {export_path} failed: {e}. "
                        f"The file is still available in the app."
                    )

            await db.commit()
            logger.info(f"Mix complete for job {job_id[:8]} — {len(segments)} segments mixed.")

        except Exception as e:
            logger.exception(f"Mix failed for job {job_id[:8]}: {e}")
            # Return to COMPLETED (not FAILED) — the session itself is fine and
            # the editor must stay usable; error_msg carries the mix failure so
            # the frontend can surface it after the mixing → completed transition.
            job.status = JobStatus.COMPLETED
            job.error_msg = f"Audio mixing failed: {e}"
            await db.commit()


async def _synthesize_all_segments(job_id: str):
    """Background task: synthesize all segments with speaker voice profiles."""
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Segment)
            .where(Segment.job_id == job_id, Segment.is_approved == True)
            .order_by(Segment.start_time)
        )
        segments = result.scalars().all()

        if not segments:
            logger.warning(f"No approved segments found for background synthesis of job {job_id}.")
            return

        # Resolve project_id for correct upload path
        j_result = await db.execute(select(Job).where(Job.id == job_id))
        job_obj = j_result.scalar_one_or_none()
        project_id = job_obj.project_id if job_obj else job_id

        job_dir = Path(settings.UPLOAD_DIR) / project_id / job_id
        tts_dir = job_dir / "tts"
        tts_dir.mkdir(parents=True, exist_ok=True)

        skipped = 0
        for seg in segments:
            # Skip segments that already have a synthesized clip — re-running
            # "synthesize job" must not re-buy TTS for unchanged lines (a text
            # edit clears tts_audio_path, so changed lines still regenerate).
            if seg.tts_audio_path and Path(seg.tts_audio_path).exists():
                skipped += 1
                continue

            voice_kwargs = await _resolve_voice(seg, db)
            out_raw_path = tts_dir / f"seg_{seg.id}_raw.wav"
            out_final_path = tts_dir / f"seg_{seg.id}.wav"

            # Release the DB connection during the (potentially minutes-long)
            # TTS call so polling requests aren't starved on the SQLite engine.
            await db.commit()

            result_data = await tts_client.synthesize(
                text=seg.khmer_text,
                output_path=str(out_raw_path),
                **voice_kwargs,
            )

            if result_data["success"]:
                from app.services.audio_extractor import apply_audio_effects
                new_duration = await apply_audio_effects(
                    input_path=str(out_raw_path),
                    output_path=str(out_final_path),
                    volume_db=seg.volume_db,
                    voice_filter=seg.voice_filter,
                    voice_speed=seg.voice_speed,
                )
                # Kept, not deleted — see the note in _synthesize_segment_db.
                seg.tts_audio_path    = str(out_final_path)
                seg.tts_duration_secs = new_duration
                await db.commit()

        if skipped:
            logger.info(f"Job synthesis: skipped {skipped} already-synthesized segment(s).")
