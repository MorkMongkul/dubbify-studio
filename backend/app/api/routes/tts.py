"""
app/api/routes/tts.py
TTS synthesis endpoints — trigger VoxCPM2 for individual segments or entire jobs.
"""
import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from pydantic import BaseModel
 
from app.core.database import get_db
from app.core.config import settings
from app.models.models import Segment, Speaker, Job, JobStatus, Voice, VoiceMode, Overlay
from app.schemas.schemas import TTSResponse
from app.services.tts_client import tts_client
from app.services.audio_extractor import mix_dubbed_audio, probe_video
from app.services.video_overlay import build_subtitle_track, render_subtitle_pngs
from app.services.pipeline_common import RUNNING_STATUSES
from app.services import auto_voice
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
            "description": voice.description,
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

    # 2 & 3. Speaker-level voice — a speaker without one gets a built-in voice
    # picked and kept (auto_voice.py) — then the legacy design prompt
    if seg.speaker_id:
        speaker = (await db.execute(select(Speaker).where(Speaker.id == seg.speaker_id))).scalar_one_or_none()
        if speaker:
            fallback = {"voice_design": speaker.voice_design_prompt or ""}
            v = None
            if speaker.voice_id:
                v = (await db.execute(select(Voice).where(Voice.id == speaker.voice_id))).scalar_one_or_none()
            if not v:
                v = await _auto_voice(db, seg, auto_voice.ensure_speaker_voice(db, speaker, seg.job_id))
            return await _voice_kwargs(v) if v else fallback

    # A line with no speaker: a fixed built-in voice by its pitch, not a random one
    v = await _auto_voice(db, seg, auto_voice.voice_for_line(db, seg))
    return await _voice_kwargs(v) if v else {"voice_design": ""}


async def _auto_voice(db: AsyncSession, seg: Segment, pick) -> Optional[Voice]:
    """A picked voice, or None when picking fails — the line is voiced the old way rather than not at all."""
    try:
        return await pick
    except Exception:  # noqa: BLE001
        logger.exception("Couldn't pick a voice automatically; using the speaker's description")
        await db.rollback()          # the session may be mid-failure; this also expires loaded rows,
        await db.refresh(seg)        # so reload the line the caller goes on to use
        return None
 
 
class TTSUnavailable(Exception):
    """Synthesis failed, or only silent mock audio came back — nothing was saved."""


def _real_tts_configured() -> bool:
    """
    True when any real engine is in the fallback chain — the free HF Space
    counts. Silent mock audio is only acceptable in pure mock mode (no engine
    configured at all); otherwise it means every engine failed or rate-limited,
    and saving it as a finished clip would put silence in a client's dub.
    """
    return bool(settings.VOXCPM2_API_URL or settings.VOXCPM2_HF_SPACE_FALLBACK or settings.GEMINI_API_KEY)


async def _render_segment_audio(seg: Segment, tts_dir: Path, voice_kwargs: dict) -> tuple[str, float, str]:
    """
    Synthesize a segment and render its final clip; returns (final_path,
    duration, backend). The new audio is written to a temp file and only
    replaces the segment's raw clip once a real synthesis succeeded — the raw
    clip is what later volume/filter/speed changes re-render from, so a failed
    regenerate must leave it (and the final clip) exactly as they were.
    Raises TTSUnavailable otherwise.
    """
    raw_path = tts_dir / f"seg_{seg.id}_raw.wav"
    final_path = tts_dir / f"seg_{seg.id}.wav"
    tmp_path = tts_dir / f".seg_{seg.id}_{uuid4().hex}.wav"
    try:
        result = await tts_client.synthesize(text=seg.khmer_text, output_path=str(tmp_path), **voice_kwargs)
        if not result.get("success"):
            raise TTSUnavailable(result.get("error") or "TTS synthesis failed")
        if result.get("mock") and _real_tts_configured():
            raise TTSUnavailable("All TTS backends failed or rate-limited — wait a minute and retry")
        os.replace(tmp_path, raw_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    from app.services.audio_extractor import apply_audio_effects
    duration = await apply_audio_effects(
        input_path=str(raw_path),
        output_path=str(final_path),
        volume_db=seg.volume_db,
        voice_filter=seg.voice_filter,
        voice_speed=seg.voice_speed,
    )
    return str(final_path), duration, result.get("backend", "")


async def _synthesize_segment_db(segment_id: str, db: AsyncSession) -> tuple[Segment, str]:
    """Returns (segment, backend) — backend is whichever of "voxcpm_gradio" /
    "voxcpm_hf_space" / "gemini" / "mock" actually produced the clip, so
    batch callers can tell when they've fallen through to Gemini (the only
    backend here with a real request-rate limit worth pacing around)."""
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

    # Release the DB connection before the TTS call — synthesis can take
    # minutes, and the dev SQLite engine has exactly one pooled connection, so
    # holding it here would block every other request (job polls, segment
    # fetches) for the whole call. expire_on_commit=False keeps `seg` usable.
    await db.commit()

    try:
        final_path, duration, backend = await _render_segment_audio(seg, tts_dir, voice_kwargs)
    except TTSUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))

    # The raw pre-effects clip is KEPT: segments.update_segment re-applies
    # effects from it whenever the user changes volume/filter/speed. Deleting
    # it made every later tweak stack on top of the baked-in one (set +6 dB,
    # regenerate, then change to +3 dB and the clip came out +9 dB).
    seg.tts_audio_path    = final_path
    seg.tts_duration_secs = duration
    await db.flush()
    return seg, backend


@router.post("/synthesize/segment/{segment_id}", response_model=TTSResponse)
async def synthesize_segment(
    segment_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Synthesize a single segment.
    """
    seg, backend = await _synthesize_segment_db(segment_id, db)
    await db.commit()
    return TTSResponse(
        segment_id=segment_id,
        audio_path=seg.tts_audio_path,
        duration_secs=seg.tts_duration_secs,
        success=True,
        error="",
        engine=backend,
        backup_voice=_is_backup_voice(backend),
    )


def _is_backup_voice(backend: str) -> bool:
    """Gemini's stock voice was used although a VoxCPM engine (the chosen voices) is set up."""
    return backend == "gemini" and bool(settings.VOXCPM2_API_URL or settings.VOXCPM2_HF_SPACE_FALLBACK)


# Each batch's progress for the editor: lines done, lines that failed (with
# the reason), lines voiced with Gemini's stock voice. Kept in memory — the
# backend is one process; after a restart the editor stops tracking.
_BATCHES: dict[str, dict] = {}
_MAX_BATCHES = 50


def _new_batch(total: int) -> str:
    batch_id = uuid4().hex
    _BATCHES[batch_id] = {"total": total, "done": 0, "failed": {}, "backup_voice": 0, "finished": False}
    while len(_BATCHES) > _MAX_BATCHES:
        _BATCHES.pop(next(iter(_BATCHES)))
    return batch_id


@router.get("/synthesize/batch/{batch_id}")
async def batch_status(batch_id: str):
    """How a voice batch is going; `finished` once every line succeeded or failed for good."""
    batch = _BATCHES.get(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="This batch is no longer tracked.")
    return {
        **batch,
        "failed": [{"segment_id": sid, "reason": reason} for sid, reason in batch["failed"].items()],
    }
 
 
class BatchSynthesizeRequest(BaseModel):
    segment_ids: List[str]
 
 
@router.post("/synthesize/batch", status_code=202)
async def synthesize_batch(
    payload: BatchSynthesizeRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db, scope="function"),
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

    batch_id = _new_batch(len(valid_ids))
    background_tasks.add_task(_synthesize_ids_task, valid_ids, batch_id)
    return {
        "message": f"Synthesis started for {len(valid_ids)} segments.",
        "batch_id": batch_id,
        "started": len(valid_ids),
        "segment_ids": valid_ids,
        "skipped": [sid for sid in payload.segment_ids if sid not in valid_ids],
    }


async def _synthesize_ids_task(segment_ids: List[str], batch_id: str | None = None) -> None:
    """Background task: synthesize the given segments with bounded concurrency
    (settings.TTS_BATCH_CONCURRENCY).

    Each concurrent worker opens its OWN DB session — never share one
    AsyncSession across concurrent coroutines, it isn't safe for that. SQLite's
    pool_size=1 means only one worker can actually hold the connection at a
    time regardless, but each session commits (releasing it) before the TTS
    call, so workers genuinely overlap during the expensive part — waiting on
    the TTS backend — and only briefly serialize on the cheap DB read/write.

    No blanket inter-segment sleep: VoxCPM2 has no request-rate limit to
    protect against, so pacing every segment regardless of which backend
    actually served it was pure dead time on the common (VoxCPM2-healthy)
    path. The only backend here with a real rate limit is Gemini TTS (hit
    only once VoxCPM2 has already failed) — `gemini_seen` turns on a short
    pacing delay for subsequent requests only once a Gemini fallback is
    actually observed in this batch, instead of assuming it up front.

    Segments that fail (rate limits, backend hiccups) get a second pass after
    a cool-off, so one bad stretch doesn't permanently skip half the batch.
    """
    from app.core.database import AsyncSessionLocal

    concurrency = max(1, settings.TTS_BATCH_CONCURRENCY)
    sem = asyncio.Semaphore(concurrency)
    gemini_seen = asyncio.Event()
    batch = _BATCHES.get(batch_id) if batch_id else None

    async def _try_one(segment_id: str) -> bool:
        async with sem:
            if gemini_seen.is_set():
                await asyncio.sleep(1.5)
            async with AsyncSessionLocal() as db:
                try:
                    _seg, backend = await _synthesize_segment_db(segment_id, db)
                    await db.commit()
                    if backend == "gemini":
                        gemini_seen.set()
                    if batch is not None:
                        batch["done"] += 1
                        batch["failed"].pop(segment_id, None)
                        batch["backup_voice"] += int(_is_backup_voice(backend))
                    return True
                except HTTPException as e:
                    logger.warning(f"Batch synthesis: segment {segment_id[:8]} failed: {e.detail}")
                    await db.rollback()
                    reason = str(e.detail)
                except Exception as e:
                    logger.exception(f"Batch synthesis: segment {segment_id[:8]} failed")
                    await db.rollback()
                    reason = f"{type(e).__name__}: {e}"
            if batch is not None:
                batch["failed"][segment_id] = reason
            return False

    try:
        results = await asyncio.gather(*[_try_one(sid) for sid in segment_ids])
        failed = [sid for sid, ok in zip(segment_ids, results) if not ok]

        if failed:
            logger.info(
                f"Batch synthesis: retrying {len(failed)} failed segment(s) "
                "after a 30s cool-off (rate limits usually clear by then)..."
            )
            await asyncio.sleep(30)
            retry_results = await asyncio.gather(*[_try_one(sid) for sid in failed])
            failed = [sid for sid, ok in zip(failed, retry_results) if not ok]
    finally:
        if batch is not None:
            batch["finished"] = True

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
    db: AsyncSession = Depends(get_db, scope="function"),
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


# External drives, where video is often exported to (macOS, then Linux).
_EXPORT_DRIVE_ROOTS = (Path("/Volumes"), Path("/media"), Path("/mnt"))


def _export_target(raw: str, default_name: str = "dubbed.mp4") -> Path:
    """
    The file a finished video may be written to: an .mp4 (or default_name
    inside an existing folder) under the user's home or on an external drive.
    The API has no login, so an arbitrary path would let any local caller
    overwrite any file the user owns. Parent folders are resolved for the
    location check; the final name never is — a symlink there gets replaced by
    the write, not followed. Raises ValueError with a user-facing reason.
    """
    dest = Path(raw).expanduser()
    if not dest.is_absolute():
        raise ValueError("export_path must be an absolute path")
    if dest.is_dir() and not dest.is_symlink():
        dest = dest / default_name
    target = dest.parent.resolve() / dest.name
    roots = (Path.home().resolve(), *_EXPORT_DRIVE_ROOTS)
    if not any(target.is_relative_to(root) for root in roots):
        raise ValueError("Export to a folder in your home directory or on an external drive.")
    if target.suffix.lower() != ".mp4":
        raise ValueError("The export file name must end in .mp4.")
    return target


def _validated_export_path(raw: str) -> str:
    """Request-time check, so a bad destination fails the request rather than the minutes-long mix."""
    try:
        target = _export_target(raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"Cannot create export folder: {e}")
    return str(Path(raw).expanduser())


def _copy_export(final_path: str, export_path: str) -> Path:
    """
    Copy the finished video to the chosen destination. The destination is
    checked again now — the mix ran for minutes since the request — and the
    bytes go to a new temp file in the destination folder that is then renamed
    over the target, so a symlink placed at the target is replaced, never
    written through. Raises OSError (PermissionError for a refused location).
    """
    try:
        target = _export_target(export_path, default_name=Path(final_path).name)
    except ValueError as e:
        raise PermissionError(str(e)) from e
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".movelo-export-", suffix=".mp4")
    try:
        with os.fdopen(fd, "wb") as out, open(final_path, "rb") as src:
            shutil.copyfileobj(src, out, 8 * 1024 * 1024)
        os.replace(tmp_name, target)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return target


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
    db: AsyncSession = Depends(get_db, scope="function"),
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
    j_result = await db.execute(select(Job).where(Job.id == job_id).with_for_update())
    job = j_result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status == JobStatus.MIXING:
        raise HTTPException(status_code=409, detail="This episode is already being exported.")
    if job.status in RUNNING_STATUSES:
        # e.g. separating music and voices again: its stems are about to change
        raise HTTPException(status_code=409, detail="This episode is still being processed. Export it when that has finished.")

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
        export_path = _validated_export_path(export_path)

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


async def _set_mix_progress(job_id: str, fraction: float) -> None:
    """
    Export progress for the UI, written in its own short transaction. Never
    raises: a progress write that fails must not stop the export. The status
    is checked in Python — on the live Postgres database jobs.status is
    VARCHAR, so comparing it to the enum in SQL fails (see
    pipeline_common.recover_orphaned_jobs).
    """
    from app.core.database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            job = await db.get(Job, job_id)
            if job and job.status == JobStatus.MIXING:
                job.progress = max(5, min(99, round(fraction * 100)))
                await db.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Couldn't save export progress for job {job_id[:8]}: {e}")


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
                        # Pillow text rendering is CPU-bound — keep it off the event loop.
                        # All lines go into ONE image stream (one ffmpeg input).
                        subs_dir = job_dir / "subs"
                        shutil.rmtree(subs_dir, ignore_errors=True)
                        subtitle_lines = await asyncio.to_thread(
                            render_subtitle_pngs,
                            segments,
                            box={"x": ov.x, "y": ov.y, "width": ov.width, "height": ov.height},
                            video_width=video_width,
                            video_height=video_height,
                            out_dir=subs_dir,
                            font_size=ov.font_size,
                            color=ov.color,
                            outline_color=ov.outline_color,
                            background_color=ov.background_color,
                        )
                        track = await asyncio.to_thread(
                            build_subtitle_track, subtitle_lines, video_width, video_height, subs_dir / "track",
                        )
                        if track:
                            video_layers.append(track)

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
                on_progress=lambda fraction: _set_mix_progress(job_id, fraction),
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
                    dest = await asyncio.to_thread(_copy_export, final_path, export_path)
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
            job.error_msg = f"Export failed: {e}"
            await db.commit()


async def _synthesize_all_segments(job_id: str):
    """Background task: synthesize all approved segments with bounded
    concurrency (settings.TTS_BATCH_CONCURRENCY). See _synthesize_ids_task
    for why each concurrent worker needs its own DB session and why the
    Gemini-only adaptive pacing (vs. a blanket per-segment sleep) is safe."""
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

    # Skip segments that already have a synthesized clip — re-running
    # "synthesize job" must not re-buy TTS for unchanged lines (a text
    # edit clears tts_audio_path, so changed lines still regenerate).
    to_synth = [s for s in segments if not resolve_existing(s.tts_audio_path)]
    skipped = len(segments) - len(to_synth)

    concurrency = max(1, settings.TTS_BATCH_CONCURRENCY)
    sem = asyncio.Semaphore(concurrency)
    gemini_seen = asyncio.Event()

    async def _synth_one(segment_id: str) -> bool:
        async with sem:
            if gemini_seen.is_set():
                await asyncio.sleep(1.5)
            async with AsyncSessionLocal() as db2:
                try:
                    seg_result = await db2.execute(select(Segment).where(Segment.id == segment_id))
                    seg = seg_result.scalar_one_or_none()
                    if not seg:
                        return False

                    voice_kwargs = await _resolve_voice(seg, db2)

                    # Release the DB connection during the (potentially
                    # minutes-long) TTS call so other concurrent workers and
                    # polling requests aren't starved on the single pooled
                    # SQLite connection.
                    await db2.commit()

                    final_path, duration, backend = await _render_segment_audio(seg, tts_dir, voice_kwargs)
                    if backend == "gemini":
                        gemini_seen.set()
                    seg.tts_audio_path    = final_path
                    seg.tts_duration_secs = duration
                    await db2.commit()
                    return True
                except TTSUnavailable as e:
                    logger.warning(f"Job synthesis: segment {segment_id[:8]} not generated: {e}")
                    await db2.rollback()
                    return False
                except Exception:
                    logger.exception(f"Job synthesis: segment {segment_id[:8]} failed")
                    await db2.rollback()
                    return False

    results = await asyncio.gather(*[_synth_one(s.id) for s in to_synth])
    failed = sum(1 for ok in results if not ok)

    if skipped:
        logger.info(f"Job synthesis: skipped {skipped} already-synthesized segment(s).")
    if failed:
        logger.warning(f"Job synthesis: {failed}/{len(to_synth)} segment(s) failed.")
