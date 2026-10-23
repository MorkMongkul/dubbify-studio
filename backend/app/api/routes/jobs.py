"""
app/api/routes/jobs.py
Job endpoints: upload video, trigger pipeline, poll status.
"""
import os
import shutil
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from typing import List

from app.core.database import get_db
from app.core.config import settings
from app.models.models import Job, Project, JobStatus, Segment, StemPiece
from app.schemas.schemas import JobResponse, PipelineStartResponse, BgmVolumeUpdate, JobTitleUpdate, JobFinishedUpdate, JobDuckingUpdate
from app.services import picture_strip, story_learning
from app.services.pipeline import (
    needs_translation, run_analysis_pipeline, run_pipeline, run_separation_retry, run_speaker_detection,
    run_translation,
)
from app.services.pipeline_common import vocals_path_for
from app.core.paths import resolve_existing
from app.services.subtitle_pipeline import run_subtitle_pipeline, run_subtitle_analysis_pipeline
from app.services.audio_extractor import extract_subtitle, frame_rate_of, list_subtitle_tracks, probe_video
from app.api.routes.overlays import apply_default_overlay_template

router = APIRouter(prefix="/jobs", tags=["Jobs"])

MAX_BYTES = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024


async def _save_upload(file: UploadFile, dest: Path) -> None:
    """Save an uploaded file to disk in chunks."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    bytes_written = 0
    with open(dest, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            bytes_written += len(chunk)
            if bytes_written > MAX_BYTES:
                out.close()
                os.remove(dest)
                raise HTTPException(
                    status_code=413,
                    detail=f"File too large. Max: {settings.MAX_UPLOAD_SIZE_MB}MB",
                )
            out.write(chunk)


@router.post(
    "/upload/{project_id}",
    response_model=PipelineStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_and_start(
    project_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Upload a video file and start the pipeline.

    Auto-detects embedded subtitles in the video:
    - If subtitles found → extracts them → uses subtitle pipeline (faster, more accurate)
    - If no subtitles    → uses ASR pipeline (Gemini transcription, no diarization)

    Supports: .mp4, .mkv, .avi, .mov, .webm
    """
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    allowed_ext = {".mp4", ".mkv", ".avi", ".mov", ".webm"}
    suffix = Path(file.filename or "video.mp4").suffix.lower()
    if suffix not in allowed_ext:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix}'. Allowed: {allowed_ext}",
        )

    # Create job record
    job = Job(project_id=project_id, status=JobStatus.PENDING, progress=0)
    db.add(job)
    await db.flush()
    await db.refresh(job)

    upload_dir = Path(settings.UPLOAD_DIR) / project_id / job.id
    video_path = upload_dir / f"source{suffix}"

    # Save video file
    try:
        await _save_upload(file, video_path)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")

    job.video_path = str(video_path)
    await db.commit()

    # Stamp the default overlay template (logo/brand kit) so every new
    # episode starts with the same layout — no per-episode logo placement.
    await apply_default_overlay_template(job, db)

    # Auto-detect embedded subtitles
    subtitle_path = await extract_subtitle(
        video_path=str(video_path),
        output_dir=str(upload_dir),
        prefer_language="chi",   # prefer Chinese subtitle track
    )

    if subtitle_path:
        # Found embedded subtitles — use faster subtitle pipeline
        logger.info(f"Job {job.id[:8]} — embedded subtitles found, using subtitle pipeline")
        job.subtitle_path = subtitle_path
        await db.commit()
        background_tasks.add_task(
            run_subtitle_pipeline, job.id, subtitle_path
        )
        message = "Embedded subtitles detected. Subtitle pipeline started (faster + more accurate)."
    else:
        # No subtitles — fall back to ASR pipeline
        logger.info(f"Job {job.id[:8]} — no subtitles found, using ASR pipeline")
        background_tasks.add_task(run_pipeline, job.id)
        message = "No embedded subtitles found. ASR pipeline started."

    return PipelineStartResponse(
        job_id=job.id,
        message=message,
        status=JobStatus.PENDING,
    )


@router.post(
    "/upload-subtitle/{project_id}",
    response_model=PipelineStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_with_subtitle(
    project_id: str,
    background_tasks: BackgroundTasks,
    video: UploadFile = File(..., description="Video file (.mp4, .mkv, etc.)"),
    subtitle: UploadFile = File(..., description="Subtitle file (.srt or .ass)"),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Upload a video + subtitle file.

    Uses subtitle-based pipeline — NO ASR needed.
    Much faster and more accurate than the ASR pipeline.
    Best for Chinese dramas and movies that include .srt or .ass subtitles.

    Flow:
      subtitle text → speaker matching → Gemini translation → done
    """
    # Verify project
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Validate video file
    video_allowed = {".mp4", ".mkv", ".avi", ".mov", ".webm"}
    video_suffix  = Path(video.filename or "video.mp4").suffix.lower()
    if video_suffix not in video_allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported video type '{video_suffix}'. Allowed: {video_allowed}",
        )

    # Validate subtitle file
    sub_allowed = {".srt", ".ass", ".ssa"}
    sub_suffix  = Path(subtitle.filename or "sub.srt").suffix.lower()
    if sub_suffix not in sub_allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported subtitle type '{sub_suffix}'. Allowed: .srt, .ass, .ssa",
        )

    # Create job
    job = Job(project_id=project_id, status=JobStatus.PENDING, progress=0)
    db.add(job)
    await db.flush()
    await db.refresh(job)

    upload_dir    = Path(settings.UPLOAD_DIR) / project_id / job.id
    video_path    = upload_dir / f"source{video_suffix}"
    subtitle_path = upload_dir / f"subtitle{sub_suffix}"

    # Save both files
    try:
        await _save_upload(video, video_path)
        await _save_upload(subtitle, subtitle_path)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")

    job.video_path = str(video_path)
    job.subtitle_path = str(subtitle_path)
    await db.commit()

    # Stamp the default overlay template (logo/brand kit) so every new
    # episode starts with the same layout — no per-episode logo placement.
    await apply_default_overlay_template(job, db)

    # Launch subtitle pipeline in background
    background_tasks.add_task(
        run_subtitle_pipeline, job.id, str(subtitle_path)
    )

    return PipelineStartResponse(
        job_id=job.id,
        message=(
            f"Subtitle pipeline started ({subtitle.filename}). "
            "Poll /jobs/{job_id} for progress."
        ),
        status=JobStatus.PENDING,
    )


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str, db: AsyncSession = Depends(get_db, scope="function")):
    """Get current job status and progress (poll this endpoint)."""
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.frame_rate is None and job.id not in _frame_rate_tried:
        # Episodes from before 0.3.0: read it once (this route is polled)
        _frame_rate_tried.add(job.id)
        video = resolve_existing(job.video_path)
        if video:
            try:
                job.frame_rate = frame_rate_of(await probe_video(str(video)))
                await db.commit()
            except Exception as e:  # noqa: BLE001 — the editor steps 1/30 s without it
                logger.warning(f"Couldn't read the frame rate of job {job_id[:8]}: {e}")
    return job


# Jobs whose missing frame rate was already looked for since the app started
_frame_rate_tried: set[str] = set()


@router.get("/{job_id}/frames")
async def get_frames(job_id: str, background_tasks: BackgroundTasks,
                     db: AsyncSession = Depends(get_db, scope="function")):
    """
    The picture strip: frame URLs, one every `interval` seconds from 0. Not
    ready yet: it's made in the background (once per job per app run) and the
    answer comes at once.
    """
    job = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    folder = Path(settings.UPLOAD_DIR) / job.project_id / job_id / "frames"
    state = picture_strip.frames_state(folder)
    if state:
        base = f"/uploads/{job.project_id}/{job_id}/frames"
        return {"ready": True, "interval": state["interval"],
                "urls": [f"{base}/f_{i:05d}.jpg" for i in range(1, state["count"] + 1)]}
    interval = picture_strip.frame_interval(job.duration_secs or 0.0)
    video = resolve_existing(job.video_path)
    if video and job_id not in _frames_started:
        _frames_started.add(job_id)
        background_tasks.add_task(picture_strip.make_frames_quietly, str(video), folder, interval)
    return {"ready": False, "interval": interval, "urls": []}


# Jobs whose picture strip was started since the app started
_frames_started: set[str] = set()


@router.get("/{job_id}/subtitle-tracks")
async def get_subtitle_tracks(job_id: str, db: AsyncSession = Depends(get_db, scope="function")):
    """
    List all subtitle tracks found in the uploaded video.
    Useful for checking what subtitle languages are available.
    """
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.video_path:
        raise HTTPException(status_code=400, detail="No video file for this job")

    tracks = await list_subtitle_tracks(job.video_path)
    return {
        "job_id":   job_id,
        "video":    Path(job.video_path).name,
        "tracks":   tracks,
        "count":    len(tracks),
    }


@router.get("/project/{project_id}", response_model=List[JobResponse])
async def list_project_jobs(project_id: str, db: AsyncSession = Depends(get_db, scope="function")):
    """List all jobs for a project."""
    result = await db.execute(
        select(Job)
        .where(Job.project_id == project_id)
        .order_by(Job.created_at.desc())
    )
    return result.scalars().all()


# Stage 2 can (re)run once Stage 1 produced audio: first Analyze from
# stems_ready, or Re-analyze of a finished/failed session. Anything else means
# work is already in flight for this job.
_ANALYZABLE_STATUSES = {JobStatus.STEMS_READY, JobStatus.COMPLETED, JobStatus.FAILED}
_RELABELABLE_STATUSES = {JobStatus.COMPLETED, JobStatus.FAILED}


async def _lock_job(db: AsyncSession, job_id: str) -> Job:
    """Load a job with a row lock (Postgres), so two clicks can't both claim it."""
    result = await db.execute(select(Job).where(Job.id == job_id).with_for_update())
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _require_audio(job: Job) -> None:
    if not vocals_path_for(job):
        raise HTTPException(
            status_code=400,
            detail="This session has no extracted audio (processing never finished) — upload the video again.",
        )


@router.post("/{job_id}/analyze", status_code=status.HTTP_202_ACCEPTED)
async def analyze_job(
    job_id: str,
    background_tasks: BackgroundTasks,
    max_speakers: int | None = None,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Trigger Stage 2: transcription + speaker labels + translation.
    Allowed at stems_ready (first run) and on completed/failed sessions
    (Re-analyze — the new segments replace the old ones only once they are
    fully ready; on failure the old ones are kept).

    max_speakers: accepted for API compatibility, ignored.
    """
    job = await _lock_job(db, job_id)
    if job.status not in _ANALYZABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"This session is busy ({job.status.value}) — wait for it to finish.",
        )
    _require_audio(job)

    # Claim the job before scheduling, in this transaction: a second click now
    # sees `diarizing` and gets a 409 instead of starting a duplicate run.
    is_subtitle_job = resolve_existing(job.subtitle_path) is not None
    job.status, job.progress, job.error_msg = JobStatus.DIARIZING, 35, ""
    await db.commit()

    if is_subtitle_job:
        background_tasks.add_task(run_subtitle_analysis_pipeline, job_id, max_speakers)
    else:
        background_tasks.add_task(run_analysis_pipeline, job_id, max_speakers)
    return {"message": "Analysis started", "job_id": job_id, "status": JobStatus.DIARIZING.value}


@router.post("/{job_id}/separate", status_code=status.HTTP_202_ACCEPTED)
async def retry_separation(
    job_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Try separating music and voices again — after the separation service
    failed and the original audio was used for both tracks. Lines,
    translations and voices are kept.
    """
    job = await _lock_job(db, job_id)
    if job.status not in _ANALYZABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"This session is busy ({job.status.value}) — wait for it to finish.",
        )
    if not resolve_existing(job.video_path):
        raise HTTPException(status_code=400, detail="This episode's video file is missing — upload it again.")
    if job.status in (JobStatus.STEMS_READY, JobStatus.COMPLETED):
        # Back to where it was: an episode waiting for Analyze keeps waiting
        # (a subtitle episode already has its lines then).
        return_status = job.status
    else:
        has_lines = (await db.execute(
            select(Segment.id).where(Segment.job_id == job_id).limit(1)
        )).scalar_one_or_none() is not None
        return_status = JobStatus.COMPLETED if has_lines else JobStatus.STEMS_READY

    job.status, job.progress, job.error_msg = JobStatus.SEPARATING, 20, ""
    await db.commit()
    background_tasks.add_task(run_separation_retry, job_id, return_status)
    return {"message": "Separating music and voices again", "job_id": job_id, "status": JobStatus.SEPARATING.value}


@router.post("/{job_id}/translate", status_code=status.HTTP_202_ACCEPTED)
async def translate_missing_lines(
    job_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Translate the lines that have no translation yet — after a translation
    outage — without re-running transcription. Translated and edited lines
    are kept as they are.
    """
    job = await _lock_job(db, job_id)
    if job.status not in _RELABELABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"This session is busy ({job.status.value}) — wait for it to finish.",
        )
    segments = (await db.execute(select(Segment).where(Segment.job_id == job_id))).scalars().all()
    missing = sum(1 for seg in segments if needs_translation(seg))
    if not missing:
        raise HTTPException(status_code=400, detail="Every line already has a translation.")

    job.status, job.progress, job.error_msg = JobStatus.TRANSLATING, 65, ""
    await db.commit()
    background_tasks.add_task(run_translation, job_id)
    return {"message": "Translation started", "job_id": job_id,
            "status": JobStatus.TRANSLATING.value, "lines": missing}


@router.post("/{job_id}/detect-speakers", status_code=status.HTTP_202_ACCEPTED)
async def detect_speakers(
    job_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Re-detect who speaks each existing segment. Text, timing, translations,
    edits and generated audio are kept — only the speaker assignment changes.
    """
    job = await _lock_job(db, job_id)
    if job.status not in _RELABELABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"This session is busy ({job.status.value}) — wait for it to finish.",
        )
    has_segments = (await db.execute(
        select(Segment.id).where(Segment.job_id == job_id).limit(1)
    )).first() is not None
    if not has_segments:
        raise HTTPException(status_code=400, detail="This session has no segments yet — run Analyze first.")
    _require_audio(job)
    if not settings.GEMINI_API_KEY:
        raise HTTPException(
            status_code=400,
            detail="Speaker detection uses Gemini — add GEMINI_API_KEY to backend/.env and restart the backend.",
        )

    job.status, job.progress, job.error_msg = JobStatus.DIARIZING, 35, ""
    await db.commit()
    background_tasks.add_task(run_speaker_detection, job_id)
    return {"message": "Speaker detection started", "job_id": job_id, "status": JobStatus.DIARIZING.value}


@router.patch("/{job_id}/title", response_model=JobResponse)
async def update_job_title(
    job_id: str,
    payload: JobTitleUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Rename a session — shown in the Sessions panel in place of the
    generic filename every upload gets stored under on disk."""
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    job.title = payload.title.strip()
    await db.commit()
    await db.refresh(job)
    return job


@router.patch("/{job_id}/ducking", response_model=JobResponse)
async def set_job_ducking(
    job_id: str,
    payload: JobDuckingUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Switch the music dipping under the voices on or off, in the preview and the export."""
    job = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.duck_music = payload.duck_music
    await db.commit()
    await db.refresh(job)
    return job


@router.patch("/{job_id}/finished", response_model=JobResponse)
async def set_job_finished(
    job_id: str,
    payload: JobFinishedUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Mark an episode finished (its translation is right) or not. Finishing
    starts learning the project's story notes from it, when a Gemini key is
    set; a run already going picks it up. Un-finishing unlearns nothing."""
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if not payload.finished:
        job.finished_at = None
    elif job.finished_at is None:
        job.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
    await db.refresh(job)
    if payload.finished and settings.GEMINI_API_KEY and story_learning.claim(job.project_id):
        background_tasks.add_task(story_learning.learn_from_finished, job.project_id)
    return job


@router.patch("/{job_id}/bgm-volume", response_model=JobResponse)
async def update_bgm_volume(
    job_id: str,
    payload: BgmVolumeUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Adjust the BGM & SFX stem's gain. Re-applies the volume filter to the
    untouched separation output each time (same never-stack-on-the-last-edit
    pattern as segment volume_db), then regenerates the waveform preview and
    playback copy so the timeline and in-editor playback both reflect the
    new level. The final export mix reads no_vocals.wav straight off disk,
    so it picks up the change automatically — no export-time parameter needed.
    """
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    job_dir = Path(settings.UPLOAD_DIR) / job.project_id / job_id
    bgm_wav = job_dir / "no_vocals.wav"
    if not bgm_wav.exists():
        raise HTTPException(status_code=400, detail="No separated BGM stem for this job yet")

    from app.services.source_separator import render_bgm_volume

    await render_bgm_volume(job_dir, payload.volume_db)

    job.bgm_volume_db = payload.volume_db
    await db.flush()
    await db.refresh(job)
    await db.commit()
    return job


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(job_id: str, db: AsyncSession = Depends(get_db, scope="function")):
    """Delete a job and all its files."""
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    job_dir = Path(settings.UPLOAD_DIR) / job.project_id / job_id
    if job_dir.exists():
        # A job dir can hold gigabytes of media — keep the delete off the event loop
        await asyncio.to_thread(shutil.rmtree, job_dir)

    # Delete child segments explicitly first (avoids relying on async ORM
    # cascade and any FK ordering issues), then the job.
    await db.execute(delete(Segment).where(Segment.job_id == job_id))
    await db.execute(delete(StemPiece).where(StemPiece.job_id == job_id))
    await db.delete(job)
    await db.commit()