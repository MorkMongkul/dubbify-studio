"""
app/api/routes/jobs.py
Job endpoints: upload video, trigger pipeline, poll status.
"""
import os
import shutil
import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List

from app.core.database import get_db
from app.core.config import settings
from app.models.models import Job, Project, JobStatus
from app.schemas.schemas import JobResponse, PipelineStartResponse
from app.services.pipeline import run_pipeline, run_analysis_pipeline
from app.services.subtitle_pipeline import run_subtitle_pipeline, run_subtitle_analysis_pipeline
from app.services.audio_extractor import extract_subtitle, list_subtitle_tracks

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
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a video file and start the pipeline.

    Auto-detects embedded subtitles in the video:
    - If subtitles found → extracts them → uses subtitle pipeline (faster, more accurate)
    - If no subtitles    → uses ASR pipeline (pyannoteAI diarize + transcribe)

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

    # Auto-detect embedded subtitles
    subtitle_path = await extract_subtitle(
        video_path=str(video_path),
        output_dir=str(upload_dir),
        prefer_language="chi",   # prefer Chinese subtitle track
    )

    if subtitle_path:
        # Found embedded subtitles — use faster subtitle pipeline
        logger.info(f"Job {job.id[:8]} — embedded subtitles found, using subtitle pipeline")
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
    db: AsyncSession = Depends(get_db),
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
    await db.commit()

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
async def get_job(job_id: str, db: AsyncSession = Depends(get_db)):
    """Get current job status and progress (poll this endpoint)."""
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/{job_id}/subtitle-tracks")
async def get_subtitle_tracks(job_id: str, db: AsyncSession = Depends(get_db)):
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
async def list_project_jobs(project_id: str, db: AsyncSession = Depends(get_db)):
    """List all jobs for a project."""
    result = await db.execute(
        select(Job)
        .where(Job.project_id == project_id)
        .order_by(Job.created_at.desc())
    )
    return result.scalars().all()


@router.post("/{job_id}/analyze", status_code=status.HTTP_202_ACCEPTED)
async def analyze_job(
    job_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger Stage 2: diarization + transcription + translation.
    Job must be in stems_ready state (Stage 1 complete).
    """
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != JobStatus.STEMS_READY:
        raise HTTPException(
            status_code=400,
            detail=f"Job must be in stems_ready state to analyze. Current state: {job.status.value}",
        )

    # Detect which pipeline to use — subtitle jobs have a subtitle_path stored
    subtitle_path = getattr(job, 'subtitle_path', None)
    if subtitle_path and Path(subtitle_path).exists():
        background_tasks.add_task(run_subtitle_analysis_pipeline, job_id)
    else:
        background_tasks.add_task(run_analysis_pipeline, job_id)

    return {"message": "Analysis started", "job_id": job_id, "status": "diarizing"}


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(job_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a job and all its files."""
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    job_dir = Path(settings.UPLOAD_DIR) / job.project_id / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir)

    await db.delete(job)