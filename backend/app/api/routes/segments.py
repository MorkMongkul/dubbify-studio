"""
app/api/routes/segments.py
Segment endpoints: review, edit translations, approve lines.
Also includes speaker management endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
import uuid

from app.core.database import get_db
from app.models.models import Segment, Speaker, Job, Project
from app.schemas.schemas import (
    SegmentResponse, SegmentUpdate, SegmentCreate,
    SpeakerResponse, SpeakerUpdate, SpeakerCreate,
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


@router.post("/jobs/{job_id}/segments", response_model=SegmentResponse, status_code=status.HTTP_201_CREATED)
async def create_segment(
    job_id: str,
    payload: SegmentCreate,
    db: AsyncSession = Depends(get_db),
):
    """Manually add a subtitle segment to a job's transcript (manual workflow)."""
    result = await db.execute(select(Job).where(Job.id == job_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Job not found")

    if payload.end_time <= payload.start_time:
        raise HTTPException(status_code=422, detail="end_time must be greater than start_time")

    seg = Segment(job_id=job_id, **payload.model_dump())
    db.add(seg)
    await db.flush()
    await db.refresh(seg)
    await db.commit()
    return seg


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

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(seg, field, value)

    effects_changed = (
        seg.volume_db != old_volume or
        seg.voice_filter != old_filter or
        seg.voice_speed != old_speed
    )

    if effects_changed and seg.tts_audio_path:
        # Effects are always re-applied to the untouched TTS output, never to
        # the already-processed file — otherwise each tweak stacks on the last.
        # Synthesis leaves this file in place (see tts._synthesize_segment_db);
        # the copy below only covers clips synthesized before that was the case.
        from app.core.paths import resolve_media_path
        final_path = resolve_media_path(seg.tts_audio_path)
        raw_path = Path(final_path).with_name(f"seg_{seg.id}_raw.wav")
        if not raw_path.exists():
            if not Path(final_path).exists():
                # Clip is gone from disk — leave the row alone rather than
                # crashing the edit; re-synthesis will recreate it.
                await db.flush(); await db.refresh(seg); await db.commit()
                return seg
            shutil.copy2(final_path, raw_path)

        from app.services.audio_extractor import apply_audio_effects
        new_duration = await apply_audio_effects(
            input_path=str(raw_path),
            output_path=final_path,
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


@router.post("/jobs/{job_id}/segments/autofit", status_code=status.HTTP_200_OK)
async def autofit_segment_speeds(job_id: str, db: AsyncSession = Depends(get_db)):
    """
    Set each generated clip's voice_speed so its audio exactly fills its
    timeline slot: audio longer than the slot speeds up, shorter slows down
    (clamped to atempo's 0.5–2.0 range). The speed is applied to the RAW
    synthesized clip (same re-apply path as the Voice Speed slider), so the
    editor preview, the slider UI, and the export all agree afterwards.
    """
    from pathlib import Path
    import shutil
    import soundfile as sf
    from app.core.paths import resolve_media_path
    from app.services.audio_extractor import apply_audio_effects

    result = await db.execute(
        select(Segment)
        .where(Segment.job_id == job_id, Segment.tts_audio_path != "")
        .order_by(Segment.start_time)
    )
    segments = result.scalars().all()
    if not segments:
        raise HTTPException(status_code=404, detail="No generated clips to fit — synthesize voices first")

    fitted = skipped = missing = 0
    for seg in segments:
        final_path = resolve_media_path(seg.tts_audio_path)
        raw_path = Path(final_path).with_name(f"seg_{seg.id}_raw.wav")
        if not raw_path.exists():
            if not Path(final_path).exists():
                missing += 1
                continue
            shutil.copy2(final_path, raw_path)

        try:
            raw_duration = sf.info(str(raw_path)).duration
        except Exception:
            missing += 1
            continue
        window = max(0.1, seg.end_time - seg.start_time)
        target = max(0.5, min(2.0, raw_duration / window))

        if abs(target - (seg.voice_speed or 1.0)) < 0.02:
            skipped += 1
            continue

        seg.voice_speed = target
        seg.tts_duration_secs = await apply_audio_effects(
            input_path=str(raw_path),
            output_path=final_path,
            volume_db=seg.volume_db,
            voice_filter=seg.voice_filter,
            voice_speed=target,
        )
        fitted += 1
        # Commit per clip: releases the (single, in SQLite dev) DB connection
        # between ffmpeg runs and lets polling clients see progress.
        await db.commit()

    await db.commit()
    return {"fitted": fitted, "skipped": skipped, "missing": missing}


@router.post("/jobs/{job_id}/segments/tidy-lanes", status_code=status.HTTP_200_OK)
async def tidy_segment_lanes(job_id: str, db: AsyncSession = Depends(get_db)):
    """
    Repack scattered clips into the minimum number of timeline lanes:
    left-to-right greedy assignment — each clip drops into the first lane
    whose previous clip has already ended, opening a new lane only for true
    time overlaps. Non-overlapping tracks collapse back to a single lane.
    """
    result = await db.execute(
        select(Segment)
        .where(Segment.job_id == job_id)
        .order_by(Segment.start_time, Segment.end_time)
    )
    segments = result.scalars().all()
    if not segments:
        raise HTTPException(status_code=404, detail="No segments found for this job")

    lane_ends: list[float] = []   # last occupied end_time per lane
    changed = 0
    for seg in segments:
        lane = next(
            (i for i, end in enumerate(lane_ends) if seg.start_time >= end - 0.001),
            None,
        )
        if lane is None:
            lane = len(lane_ends)
            lane_ends.append(seg.end_time)
        else:
            lane_ends[lane] = seg.end_time
        if (seg.lane_index or 0) != lane:
            seg.lane_index = lane
            changed += 1

    await db.commit()
    return {"changed": changed, "lanes": len(lane_ends)}


# ── Speakers ──────────────────────────────────────────────────

@router.get("/projects/{project_id}/speakers", response_model=List[SpeakerResponse])
async def list_speakers(project_id: str, db: AsyncSession = Depends(get_db)):
    """Get all speakers detected in a project.

    Ordered by label so the list is stable across requests: the frontend derives
    each speaker's timeline colour from its position in this array, and an
    unordered SELECT lets Postgres hand back a different order after row
    updates, which would reshuffle every speaker's colour mid-session.
    """
    result = await db.execute(
        select(Speaker).where(Speaker.project_id == project_id).order_by(Speaker.label)
    )
    return result.scalars().all()


@router.post("/projects/{project_id}/speakers", response_model=SpeakerResponse, status_code=status.HTTP_201_CREATED)
async def create_speaker(
    project_id: str,
    payload: SpeakerCreate,
    db: AsyncSession = Depends(get_db),
):
    """Manually create a reusable speaker profile (manual workflow)."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    data = payload.model_dump()
    if not data.get("label"):
        data["label"] = f"MANUAL_{uuid.uuid4().hex[:8]}"

    speaker = Speaker(project_id=project_id, **data)
    db.add(speaker)
    await db.flush()
    await db.refresh(speaker)
    await db.commit()
    return speaker


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

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(speaker, field, value)

    await db.flush()
    await db.refresh(speaker)
    return speaker
