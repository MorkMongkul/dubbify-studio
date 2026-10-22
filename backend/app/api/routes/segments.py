"""
app/api/routes/segments.py
Segment endpoints: review, edit translations, approve lines.
Also includes speaker management endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified
from typing import List
import uuid
from pathlib import Path

from app.core.config import settings
from app.core.database import get_db
from app.core.paths import resolve_media_path
from app.models.models import Segment, Speaker, Job, Project, Voice
from app.schemas.schemas import (
    SegmentResponse, SegmentUpdate, SegmentCreate,
    SpeakerResponse, SpeakerUpdate, SpeakerCreate, SpeakerMerge, SpeakerMergeResponse, SpeakerUnmerge,
)
from app.services import story_notes

router = APIRouter(tags=["Segments & Speakers"])


# ── Segments ──────────────────────────────────────────────────

@router.get("/jobs/{job_id}/segments", response_model=List[SegmentResponse])
async def list_segments(job_id: str, db: AsyncSession = Depends(get_db, scope="function")):
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
    db: AsyncSession = Depends(get_db, scope="function"),
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


async def _clip_path_in_session(db: AsyncSession, seg: Segment, path: str) -> str:
    """
    A client may point a segment back at one of its own generated clips (undo
    of a text edit does), but never at any other file: changing volume/filter/
    speed later re-renders that file in place with ffmpeg.
    """
    job = (await db.execute(select(Job).where(Job.id == seg.job_id))).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    tts_dir = (Path(settings.UPLOAD_DIR) / job.project_id / job.id / "tts").resolve()
    resolved = Path(resolve_media_path(path)).resolve()
    if not resolved.is_relative_to(tts_dir):
        raise HTTPException(status_code=400, detail="Audio clips must come from this session's generated voices.")
    # Store the path that was checked, never the client's spelling of it: a raw
    # path that only remapped into the session now (resolve_media_path) would
    # resolve to its own location once a file exists there, bypassing the check.
    return str(resolved)


@router.patch("/segments/{segment_id}", response_model=SegmentResponse)
async def update_segment(
    segment_id: str,
    payload: SegmentUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
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
    if update_data.get("tts_audio_path"):
        update_data["tts_audio_path"] = await _clip_path_in_session(db, seg, update_data["tts_audio_path"])
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
async def delete_segment(segment_id: str, db: AsyncSession = Depends(get_db, scope="function")):
    """Delete a single segment (clip) from the timeline."""
    result = await db.execute(select(Segment).where(Segment.id == segment_id))
    seg = result.scalar_one_or_none()
    if not seg:
        raise HTTPException(status_code=404, detail="Segment not found")
    await db.delete(seg)
    await db.commit()


@router.post("/segments/{segment_id}/approve", response_model=SegmentResponse)
async def approve_segment(segment_id: str, db: AsyncSession = Depends(get_db, scope="function")):
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
async def approve_all_segments(job_id: str, db: AsyncSession = Depends(get_db, scope="function")):
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
async def autofit_segment_speeds(job_id: str, db: AsyncSession = Depends(get_db, scope="function")):
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
async def tidy_segment_lanes(job_id: str, db: AsyncSession = Depends(get_db, scope="function")):
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
async def list_speakers(project_id: str, db: AsyncSession = Depends(get_db, scope="function")):
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
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Manually create a reusable speaker profile (manual workflow)."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    data = payload.model_dump()
    if not data.get("label"):
        data["label"] = f"MANUAL_{uuid.uuid4().hex[:8]}"
    if data.get("voice_id") and not await db.get(Voice, data["voice_id"]):
        raise HTTPException(status_code=404, detail="Voice not found")

    speaker = Speaker(project_id=project_id, voice_auto=False, **data)
    db.add(speaker)
    await db.flush()
    await db.refresh(speaker)
    await db.commit()
    return speaker


@router.patch("/speakers/{speaker_id}", response_model=SpeakerResponse)
async def update_speaker(
    speaker_id: str,
    payload: SpeakerUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
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
    if update_data.get("voice_id") and not await db.get(Voice, update_data["voice_id"]):
        raise HTTPException(status_code=404, detail="Voice not found")
    for field, value in update_data.items():
        setattr(speaker, field, value)
    if "voice_id" in update_data:
        speaker.voice_auto = False      # chosen by the user, not picked by the app

    await db.flush()
    await db.refresh(speaker)
    return speaker


@router.post("/speakers/{speaker_id}/merge", response_model=SpeakerMergeResponse)
async def merge_speaker(
    speaker_id: str,
    payload: SpeakerMerge,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Move this speaker's lines, in every episode, to another speaker of the same
    project — the same person under two labels — and remove this speaker. The
    Story character linked to its label follows. The response is what
    /unmerge needs to undo it. Lines already voiced keep their audio until
    voiced again, as when a speaker's voice changes.
    """
    source = await db.get(Speaker, speaker_id)
    target = await db.get(Speaker, payload.into)
    if not source or not target:
        raise HTTPException(status_code=404, detail="Speaker not found")
    if source.id == target.id or source.project_id != target.project_id:
        raise HTTPException(status_code=400, detail="Choose another speaker of this project")
    lines = (await db.execute(select(Segment).where(Segment.speaker_id == source.id))).scalars().all()
    for line in lines:
        line.speaker_id = target.id
    removed = SpeakerResponse.model_validate(source)
    project = await db.get(Project, source.project_id)
    project.story_notes, relinked = story_notes.relink_speaker(project.story_notes, source.label, target.label)
    flag_modified(project, "story_notes")
    await db.flush()
    await db.delete(source)
    await db.commit()
    return SpeakerMergeResponse(moved=[line.id for line in lines], removed=removed, relinked=relinked)


@router.post("/speakers/{speaker_id}/unmerge", response_model=SpeakerResponse)
async def unmerge_speaker(
    speaker_id: str,
    payload: SpeakerUnmerge,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Undo a merge into this speaker, in one step: the merged-away speaker comes
    back as it was (label, name, voice and whether the app picked it, gender,
    age, description), the moved lines that are still on this speaker go back
    to it (one moved on since stays where it is), and its Story characters are
    linked to it again.
    """
    target = await db.get(Speaker, speaker_id)
    if not target:
        raise HTTPException(status_code=404, detail="Speaker not found")
    snap = payload.removed
    voice_id = snap.voice_id if snap.voice_id and await db.get(Voice, snap.voice_id) else None
    speaker = Speaker(
        project_id=target.project_id, label=snap.label, display_name=snap.display_name, gender=snap.gender,
        age_group=snap.age_group, voice_design_prompt=snap.voice_design_prompt, voice_id=voice_id,
        voice_auto=snap.voice_auto and voice_id is not None,
    )
    db.add(speaker)
    await db.flush()
    if payload.moved:
        lines = (await db.execute(select(Segment).where(
            Segment.id.in_(payload.moved), Segment.speaker_id == target.id))).scalars().all()
        for line in lines:
            line.speaker_id = speaker.id
    if payload.relinked:
        project = await db.get(Project, target.project_id)
        project.story_notes = story_notes.link_characters(project.story_notes, payload.relinked, snap.label)
        flag_modified(project, "story_notes")
    await db.commit()
    await db.refresh(speaker)
    return speaker
