"""
app/api/routes/segments.py
Segment endpoints: review, edit translations, approve lines.
Also includes speaker management endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List

from app.core.database import get_db
from app.models.models import Segment, Speaker, Job
from app.schemas.schemas import (
    SegmentResponse, SegmentUpdate,
    SpeakerResponse, SpeakerUpdate,
)

router = APIRouter(tags=["Segments & Speakers"])


# ── Segments ──────────────────────────────────────────────────

@router.get("/jobs/{job_id}/segments", response_model=List[SegmentResponse])
async def list_segments(job_id: str, db: AsyncSession = Depends(get_db)):
    """Get all transcript segments for a job, ordered by start time."""
    result = await db.execute(
        select(Segment)
        .where(Segment.job_id == job_id)
        .order_by(Segment.start_time)
    )
    return result.scalars().all()


@router.patch("/segments/{segment_id}", response_model=SegmentResponse)
async def update_segment(
    segment_id: str,
    payload: SegmentUpdate,
    db: AsyncSession = Depends(get_db),
):
    """
    Update a segment's text or approval status.
    Used by the script editor — editors can fix translation errors.
    """
    from pathlib import Path
    import shutil

    result = await db.execute(select(Segment).where(Segment.id == segment_id))
    seg = result.scalar_one_or_none()
    if not seg:
        raise HTTPException(status_code=404, detail="Segment not found")

    # Capture old parameters to check for changes
    old_volume = seg.volume_db
    old_filter = seg.voice_filter
    old_speed = seg.voice_speed

    update_data = payload.model_dump(exclude_none=True)
    for field, value in update_data.items():
        setattr(seg, field, value)

    effects_changed = (
        seg.volume_db != old_volume or
        seg.voice_filter != old_filter or
        seg.voice_speed != old_speed
    )

    if effects_changed and seg.tts_audio_path:
        raw_path = Path(seg.tts_audio_path).with_name(f"seg_{seg.id}_raw.wav")
        if not raw_path.exists():
            shutil.copy2(seg.tts_audio_path, raw_path)

        from app.services.audio_extractor import apply_audio_effects
        new_duration = await apply_audio_effects(
            input_path=str(raw_path),
            output_path=seg.tts_audio_path,
            volume_db=seg.volume_db,
            voice_filter=seg.voice_filter,
            voice_speed=seg.voice_speed,
        )
        seg.tts_duration_secs = new_duration

    await db.flush()
    await db.refresh(seg)
    await db.commit()
    return seg


@router.delete("/segments/{segment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_segment(segment_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a single segment (clip) from the timeline."""
    result = await db.execute(select(Segment).where(Segment.id == segment_id))
    seg = result.scalar_one_or_none()
    if not seg:
        raise HTTPException(status_code=404, detail="Segment not found")
    await db.delete(seg)
    await db.commit()


@router.post("/segments/{segment_id}/approve", response_model=SegmentResponse)
async def approve_segment(segment_id: str, db: AsyncSession = Depends(get_db)):
    """Mark a segment as approved for TTS synthesis."""
    result = await db.execute(select(Segment).where(Segment.id == segment_id))
    seg = result.scalar_one_or_none()
    if not seg:
        raise HTTPException(status_code=404, detail="Segment not found")
    seg.is_approved = True
    await db.flush()
    await db.refresh(seg)
    return seg


@router.post("/jobs/{job_id}/approve-all", status_code=status.HTTP_200_OK)
async def approve_all_segments(job_id: str, db: AsyncSession = Depends(get_db)):
    """Approve all segments in a job for bulk TTS synthesis."""
    result = await db.execute(select(Segment).where(Segment.job_id == job_id))
    segments = result.scalars().all()
    if not segments:
        raise HTTPException(status_code=404, detail="No segments found for this job")

    for seg in segments:
        seg.is_approved = True
    await db.flush()

    return {"approved": len(segments), "job_id": job_id}


# ── Speakers ──────────────────────────────────────────────────

@router.get("/projects/{project_id}/speakers", response_model=List[SpeakerResponse])
async def list_speakers(project_id: str, db: AsyncSession = Depends(get_db)):
    """Get all speakers detected in a project."""
    result = await db.execute(
        select(Speaker).where(Speaker.project_id == project_id)
    )
    return result.scalars().all()


@router.patch("/speakers/{speaker_id}", response_model=SpeakerResponse)
async def update_speaker(
    speaker_id: str,
    payload: SpeakerUpdate,
    db: AsyncSession = Depends(get_db),
):
    """
    Update speaker profile — name, gender, age group, voice design prompt.
    Voice design prompt feeds directly into VoxCPM2.
    """
    result = await db.execute(select(Speaker).where(Speaker.id == speaker_id))
    speaker = result.scalar_one_or_none()
    if not speaker:
        raise HTTPException(status_code=404, detail="Speaker not found")

    update_data = payload.model_dump(exclude_none=True)
    for field, value in update_data.items():
        setattr(speaker, field, value)

    await db.flush()
    await db.refresh(speaker)
    return speaker
