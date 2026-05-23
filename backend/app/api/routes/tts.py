"""
app/api/routes/tts.py
TTS synthesis endpoints — trigger VoxCPM2 for individual segments or entire jobs.
"""
import asyncio
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List

from app.core.database import get_db
from app.core.config import settings
from app.models.models import Segment, Speaker, Job, JobStatus
from app.schemas.schemas import TTSResponse
from app.services.tts_client import tts_client
from app.services.audio_extractor import mix_dubbed_audio

router = APIRouter(prefix="/tts", tags=["TTS Synthesis"])


@router.post("/synthesize/segment/{segment_id}", response_model=TTSResponse)
async def synthesize_segment(
    segment_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Synthesize a single segment using VoxCPM2.
    Uses the speaker's voice_design_prompt automatically.
    """
    result = await db.execute(select(Segment).where(Segment.id == segment_id))
    seg = result.scalar_one_or_none()
    if not seg:
        raise HTTPException(status_code=404, detail="Segment not found")

    if not seg.khmer_text.strip():
        raise HTTPException(status_code=400, detail="Segment has no Khmer text to synthesize")

    # Get speaker's voice design prompt
    voice_design = ""
    if seg.speaker_id:
        sp_result = await db.execute(select(Speaker).where(Speaker.id == seg.speaker_id))
        speaker = sp_result.scalar_one_or_none()
        if speaker:
            voice_design = speaker.voice_design_prompt

    # Build output path
    job_dir = Path(settings.UPLOAD_DIR) / seg.job_id
    tts_dir = job_dir / "tts"
    tts_dir.mkdir(parents=True, exist_ok=True)
    output_path = tts_dir / f"seg_{segment_id}.wav"

    # Call VoxCPM2
    result_data = await tts_client.synthesize(
        text=seg.khmer_text,
        voice_design=voice_design,
        output_path=str(output_path),
    )

    if result_data["success"]:
        seg.tts_audio_path    = result_data["audio_path"]
        seg.tts_duration_secs = result_data["duration_secs"]
        await db.flush()

    return TTSResponse(
        segment_id=segment_id,
        audio_path=result_data["audio_path"],
        duration_secs=result_data["duration_secs"],
        success=result_data["success"],
        error=result_data.get("error", ""),
    )


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

    background_tasks.add_task(_synthesize_all_segments, job_id, segments, db)

    return {
        "message": f"TTS synthesis started for {len(segments)} approved segments.",
        "job_id": job_id,
        "segment_count": len(segments),
    }


@router.post("/mix/{job_id}")
async def mix_final_audio(
    job_id: str,
    mute_original: bool = True,
    db: AsyncSession = Depends(get_db),
):
    """
    Mix all synthesized TTS segments back into the original video.
    Produces the final dubbed .mp4 file.
    """
    # Get job
    j_result = await db.execute(select(Job).where(Job.id == job_id))
    job = j_result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Get segments that have TTS audio
    result = await db.execute(
        select(Segment)
        .where(Segment.job_id == job_id, Segment.tts_audio_path != "")
        .order_by(Segment.start_time)
    )
    segments = result.scalars().all()

    if not segments:
        raise HTTPException(status_code=400, detail="No TTS audio found. Run synthesis first.")

    tts_seg_list = [
        {
            "start_time": seg.start_time,
            "audio_path": seg.tts_audio_path,
            "duration": seg.tts_duration_secs,
        }
        for seg in segments
    ]

    output_path = str(Path(settings.UPLOAD_DIR) / job.project_id / job_id / "dubbed_output.mp4")

    try:
        final_path = await mix_dubbed_audio(
            video_path=job.video_path,
            tts_segments=tts_seg_list,
            output_path=output_path,
            mute_original=mute_original,
        )
        job.output_path = final_path
        job.status = JobStatus.COMPLETED
        await db.flush()

        return {
            "success": True,
            "output_path": final_path,
            "segments_mixed": len(segments),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Audio mixing failed: {e}")


async def _synthesize_all_segments(job_id: str, segments: list, db: AsyncSession):
    """Background task: synthesize all segments with speaker voice profiles."""
    job_dir = Path(settings.UPLOAD_DIR) / job_id
    tts_dir = job_dir / "tts"
    tts_dir.mkdir(parents=True, exist_ok=True)

    # Build speaker cache to avoid repeated DB lookups
    speaker_cache: dict[str, str] = {}

    for seg in segments:
        voice_design = ""
        if seg.speaker_id:
            if seg.speaker_id not in speaker_cache:
                sp_result = await db.execute(
                    select(Speaker).where(Speaker.id == seg.speaker_id)
                )
                speaker = sp_result.scalar_one_or_none()
                speaker_cache[seg.speaker_id] = speaker.voice_design_prompt if speaker else ""
            voice_design = speaker_cache[seg.speaker_id]

        out_path = tts_dir / f"seg_{seg.id}.wav"
        result_data = await tts_client.synthesize(
            text=seg.khmer_text,
            voice_design=voice_design,
            output_path=str(out_path),
        )

        if result_data["success"]:
            seg.tts_audio_path    = result_data["audio_path"]
            seg.tts_duration_secs = result_data["duration_secs"]
            await db.flush()
