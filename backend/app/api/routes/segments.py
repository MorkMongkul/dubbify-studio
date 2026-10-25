"""
app/api/routes/segments.py
Segment endpoints: review, edit translations, approve lines.
Also includes speaker management endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select
from sqlalchemy.orm.attributes import flag_modified
from typing import List
import json
import uuid
from pathlib import Path

from app.core.config import settings
from app.core.database import get_db
from app.core.paths import resolve_media_path
from app.models.models import LinePrint, Segment, Speaker, Job, Project, Voice
from app.schemas.schemas import (
    CharacterPick, CharacterPicked, SegmentResponse, SegmentUpdate, SegmentCreate, LayoutRequest, LayoutResponse, SplitRequest, SplitResponse,
    SpeakerResponse, SpeakerUpdate, SpeakerCreate, SpeakerMerge, SpeakerMergeResponse, CharacterSuggestion, CharacterDismiss, SpeakerUnmerge,
)
from app.services import story_notes, timeline, voice_level
from app.services.audio_extractor import voice_at_rate

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
    # A line with no voice is as long as its text; on a track where it would
    # overlap another clip, it goes to the nearest free one (never in time)
    timeline.follow_text(seg)
    db.add(seg)
    await db.flush()
    await db.commit()
    await timeline.settle_job(db, job_id, [seg.id])
    await db.refresh(seg)
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


def _apply_timing(seg: Segment, sent: set[str], moved_from: float | None = None) -> None:
    """
    The timeline's rules after an edit (app/services/timeline.py). A voiced
    clip stays inside its voice: an edge cuts sound, never changes the speed;
    a clip moved by its start alone keeps its length. A voiceless line is as
    long as its text takes to say (an end sent for it is ignored), unless it
    has no text. `sent`: the fields the client sent; `moved_from`: the start
    before the edit.
    """
    if timeline.is_voiced(seg):
        if "start_time" in sent and not ({"end_time", "voice_offset"} & sent) and moved_from is not None:
            seg.end_time += seg.start_time - moved_from
        timeline.clamp_voiced(seg)
    elif not timeline.follow_text(seg) and seg.end_time < seg.start_time + timeline.MIN_CLIP_SECS:
        seg.end_time = seg.start_time + timeline.MIN_CLIP_SECS


# Fields whose change can make a clip overlap another on its track
_LAYOUT_FIELDS = {"start_time", "end_time", "lane_index", "voice_offset", "khmer_text", "voice_speed", "tts_audio_path"}


@router.patch("/segments/{segment_id}", response_model=SegmentResponse)
async def update_segment(
    segment_id: str,
    payload: SegmentUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Update a segment's text, timing, effects or approval.

    Editing the Khmer text never touches the voice (the clip shows it was made
    from older text). A clip that ends up overlapping another on its track
    moves to the nearest track where it fits — never in time.
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
    old_speed = seg.voice_speed or 1.0
    # The speed heard: the file's own × fit_rate (an upgraded voice can carry both)
    old_total = timeline.total_speed(seg)
    old_start = seg.start_time

    update_data = payload.model_dump(exclude_unset=True)
    if (update_data.get("speaker_id") not in (None, seg.speaker_id)
            and "who_confirmed" not in update_data and "who_check" not in update_data):
        # Choosing another character is a decision: the line is confirmed. (A voice change is casting only.)
        update_data["who_confirmed"], update_data["who_check"] = True, None
    if update_data.get("tts_audio_path"):
        update_data["tts_audio_path"] = await _clip_path_in_session(db, seg, update_data["tts_audio_path"])
    if "who_check" in update_data:
        update_data["who_check"] = None if update_data["who_check"] is None else json.dumps(update_data["who_check"])
    for field, value in update_data.items():
        setattr(seg, field, value)
    if update_data.get("tts_audio_path") == "":
        # The voice is gone: so is the part of it the clip played
        seg.voice_offset, seg.fit_rate, seg.voice_text = 0.0, 1.0, None

    # The editor sends the speed the user hears; it becomes the file's own speed
    new_speed = seg.voice_speed or 1.0
    speed_changed = "voice_speed" in update_data and (
        abs(new_speed - old_total) > 1e-6 or abs(new_speed - old_speed) > 1e-6)
    if speed_changed and timeline.is_voiced(seg):
        # Keep the same words cut at the new speed (the file is re-rendered below)
        seg.voice_speed = old_speed
        timeline.rescale_for_speed(seg, new_speed)

    effects_changed = (
        seg.volume_db != old_volume or
        seg.voice_filter != old_filter or
        speed_changed
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

        # With the level decided when the voice was made (voice_level), not re-measured
        seg.tts_duration_secs = await voice_level.render_clip(seg, raw_path, Path(final_path))

    _apply_timing(seg, set(update_data), moved_from=old_start)

    await db.flush()
    await db.commit()
    if _LAYOUT_FIELDS & set(update_data):
        await timeline.settle_job(db, seg.job_id, [seg.id])
    await db.refresh(seg)
    return seg


@router.put("/jobs/{job_id}/segments/layout", response_model=LayoutResponse)
async def update_layout(
    job_id: str,
    payload: LayoutRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Move, trim and re-track several clips at once (a drag, a group move, an
    undo): every change is applied before the tracks are settled, so clips can
    swap places. Returns every clip whose place changed, including clips the
    server moved to another track.
    """
    segs = (await db.execute(select(Segment).where(Segment.job_id == job_id))).scalars().all()
    by_id = {s.id: s for s in segs}
    missing = [c.id for c in payload.changes if c.id not in by_id]
    if missing:
        raise HTTPException(status_code=404, detail="Segment not found in this session")

    def place(s: Segment) -> tuple:
        return (s.start_time, s.end_time, s.lane_index or 0, timeline.offset_of(s), timeline.fades_of(s))

    before = {s.id: place(s) for s in segs}
    for change in payload.changes:
        seg = by_id[change.id]
        sent = change.model_dump(exclude_unset=True, exclude={"id"})
        old_start = seg.start_time
        for field, value in sent.items():
            if value is not None:
                setattr(seg, field, value)
        _apply_timing(seg, {k for k, v in sent.items() if v is not None}, moved_from=old_start)
    if payload.track_flags is not None:
        # A drop that inserted a track moved the tracks below it: their switches move too
        job = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
        if job:
            job.track_flags = timeline.flags_to(timeline.prune_flags(timeline.flags_from(payload.track_flags), segs))
    await db.commit()
    await timeline.settle_job(db, job_id, [c.id for c in payload.changes])
    changed = [s for s in segs if place(s) != before[s.id]]
    return LayoutResponse(segments=sorted(changed, key=lambda s: (s.start_time, s.id)))


def _copy_line(seg: Segment) -> Segment:
    """A new line with every field of `seg` and its own copy of the voice files
    (voices are stored and re-rendered per line)."""
    from app.core.paths import resolve_media_path
    from app.models.models import generate_uuid
    import shutil

    fields = {c.key: getattr(seg, c.key) for c in Segment.__table__.columns if c.key != "id"}
    copy = Segment(id=generate_uuid(), **fields)
    if seg.tts_audio_path:
        final = Path(resolve_media_path(seg.tts_audio_path))
        if final.exists():
            new_final = final.with_name(f"seg_{copy.id}.wav")
            shutil.copy2(final, new_final)
            raw = final.with_name(f"seg_{seg.id}_raw.wav")
            if raw.exists():
                shutil.copy2(raw, final.with_name(f"seg_{copy.id}_raw.wav"))
            copy.tts_audio_path = str(new_final)
    return copy


async def _segment_or_404(db: AsyncSession, segment_id: str) -> Segment:
    seg = (await db.execute(select(Segment).where(Segment.id == segment_id))).scalar_one_or_none()
    if not seg:
        raise HTTPException(status_code=404, detail="Segment not found")
    return seg


async def _refresh_faces(db: AsyncSession, project_id: str) -> None:
    from app.services.characters import upgrade   # here, so the backend starts without the characters package
    await upgrade.refresh_faces_safely(db, project_id)


@router.post("/segments/{segment_id}/character", response_model=CharacterPicked)
async def pick_character(
    segment_id: str,
    payload: CharacterPick,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    The user picks who speaks this line: it is confirmed for that speaker, and — when nobody
    edited its Khmer — translated again for them. The pick is saved first and never fails because
    of the translation (retranslated=False then). previous_khmer lets the editor undo it.
    """
    from app.services import pipeline

    seg = await _segment_or_404(db, segment_id)
    job = (await db.execute(select(Job).where(Job.id == seg.job_id))).scalar_one()
    speaker = (await db.execute(select(Speaker).where(Speaker.id == payload.speaker_id))).scalar_one_or_none()
    if speaker is None or speaker.project_id != job.project_id:
        raise HTTPException(status_code=400, detail="That character isn't in this project")
    previous = seg.khmer_text
    seg.speaker_id, seg.who_confirmed, seg.who_check = speaker.id, True, None
    await db.commit()   # the pick stands whatever the translation does, and the connection is free for Gemini
    await _refresh_faces(db, job.project_id)   # the line's face may now be its character's best
    retranslated = await pipeline.retranslate_line(db, seg.id)
    seg = (await db.execute(select(Segment).where(Segment.id == segment_id)
                            .execution_options(populate_existing=True))).scalar_one()
    return CharacterPicked(segment=SegmentResponse.model_validate(seg), retranslated=retranslated,
                           previous_khmer=previous or "")


@router.post("/segments/{segment_id}/split", response_model=SplitResponse)
async def split_segment(
    segment_id: str,
    payload: SplitRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Split a clip at `at`, like CapCut: a voiced clip's halves each play their
    own part of the one voice (the second with its own copy of the files); the
    texts are split at the phrase break nearest to the cut; the actor's span is
    split at the cut. A voiceless clip's halves each follow their own text.
    """
    seg = await _segment_or_404(db, segment_id)
    at = payload.at
    if not (seg.start_time + timeline.MIN_CLIP_SECS < at < seg.end_time - timeline.MIN_CLIP_SECS):
        raise HTTPException(status_code=422, detail="Split point is too close to the clip's edge")

    share = (at - seg.start_time) / (seg.end_time - seg.start_time)
    voiced = timeline.is_voiced(seg)
    outdated = seg.voice_text is not None and seg.voice_text != seg.khmer_text
    second = _copy_line(seg)
    texts = {f: timeline.split_text(getattr(seg, f) or "", share) for f in ("khmer_text", "source_text", "english_text")}
    for field, (left, right) in texts.items():
        setattr(seg, field, left)
        setattr(second, field, right)
    if voiced and seg.voice_text is not None and not outdated:
        seg.voice_text, second.voice_text = seg.khmer_text, second.khmer_text

    # Where the actor spoke, cut at the same time; a half wholly outside it has none
    if seg.source_start is not None and seg.source_end is not None:
        src_start, src_end = seg.source_start, seg.source_end
        seg.source_start, seg.source_end = (src_start, min(src_end, at)) if src_start < at else (None, None)
        second.source_start, second.source_end = (max(src_start, at), src_end) if src_end > at else (None, None)

    second.start_time = at
    # Each half keeps its outer fade
    seg.fade_out_secs = 0.0
    second.fade_in_secs = 0.0
    if voiced:
        second.voice_offset = timeline.offset_of(seg) + (at - seg.start_time) * timeline.rate_of(seg)
        seg.end_time = at
        timeline.clamp_voiced(seg)
        timeline.clamp_voiced(second)
    else:
        seg.end_time = at
        timeline.follow_text(seg)
        timeline.follow_text(second)
    # The first half keeps the id, but its print described the whole line
    await db.execute(delete(LinePrint).where(LinePrint.segment_id == seg.id))
    db.add(second)
    await db.commit()
    await timeline.settle_job(db, seg.job_id, [seg.id, second.id])
    await db.refresh(seg)
    await db.refresh(second)
    return SplitResponse(first=seg, second=second)


@router.post("/segments/{segment_id}/duplicate", response_model=SegmentResponse, status_code=status.HTTP_201_CREATED)
async def duplicate_segment(segment_id: str, db: AsyncSession = Depends(get_db, scope="function")):
    """A copy of the clip — voice included, with its own files — right after it."""
    seg = await _segment_or_404(db, segment_id)
    copy = _copy_line(seg)
    length = seg.end_time - seg.start_time
    copy.start_time = seg.end_time
    copy.end_time = seg.end_time + length
    # The copy wasn't spoken by the actor there
    copy.source_start = copy.source_end = None
    db.add(copy)
    await db.commit()
    await timeline.settle_job(db, seg.job_id, [copy.id])
    await db.refresh(copy)
    return copy


@router.delete("/segments/{segment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_segment(segment_id: str, db: AsyncSession = Depends(get_db, scope="function")):
    """Delete a single segment (clip) from the timeline."""
    result = await db.execute(select(Segment).where(Segment.id == segment_id))
    seg = result.scalar_one_or_none()
    if not seg:
        raise HTTPException(status_code=404, detail="Segment not found")
    await db.execute(delete(LinePrint).where(LinePrint.segment_id == segment_id))
    await db.delete(seg)
    await db.commit()


@router.get("/segments/{segment_id}/voice")
async def voice_at_speed(
    segment_id: str,
    rate: float = Query(..., gt=1.0, le=4.0),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """A clip's voice at its speed, made with the export's filter, for the editor's preview."""
    seg = (await db.execute(select(Segment).where(Segment.id == segment_id))).scalar_one_or_none()
    path = Path(resolve_media_path(seg.tts_audio_path)) if seg and seg.tts_audio_path else None
    await db.commit()  # no transaction held while ffmpeg runs
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="This line has no voice")
    try:
        out = await voice_at_rate(path, rate)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return FileResponse(out, media_type="audio/wav")


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
    Speed up each voice that runs into a later line — just enough to end
    before it starts (an overlap the original already had doesn't count) and
    never above timeline.AUTOFIT_FASTEST. Never slows a voice down, never
    lowers a speed the user set. The new speed is rendered into the voice file
    (as the speed control does) and the clip keeps the same words. Returns how
    many voices it sped up and how many still run into the next line.
    """
    import shutil
    from app.core.paths import resolve_media_path

    segs = (await db.execute(
        select(Segment).where(Segment.job_id == job_id).order_by(Segment.start_time)
    )).scalars().all()
    voiced = [s for s in segs if timeline.is_voiced(s)]
    if not voiced:
        raise HTTPException(status_code=404, detail="No generated clips to fit — synthesize voices first")

    fitted = left = 0
    changed: list[str] = []
    for seg in voiced:
        target = timeline.overrun_target(seg, segs)
        if target is None:
            continue
        current = timeline.total_speed(seg)
        if current >= timeline.AUTOFIT_FASTEST - 0.005:
            left += 1
            continue
        needed = current * (seg.end_time - seg.start_time) / max(timeline.MIN_CLIP_SECS, target - seg.start_time)
        new_speed = round(min(timeline.AUTOFIT_FASTEST, needed), 3)

        final_path = Path(resolve_media_path(seg.tts_audio_path))
        raw_path = final_path.with_name(f"seg_{seg.id}_raw.wav")
        if not raw_path.exists():
            if not final_path.exists():
                left += 1     # the voice is gone from disk: nothing to speed up
                continue
            shutil.copy2(final_path, raw_path)
        # The file's own speed carries the whole speed from now on (fit_rate 1)
        timeline.rescale_for_speed(seg, new_speed)
        seg.tts_duration_secs = await voice_level.render_clip(seg, raw_path, final_path)
        timeline.clamp_voiced(seg)
        fitted += 1
        changed.append(seg.id)
        if new_speed < needed - 0.005:
            left += 1
        # Commit per clip: releases the (single, in SQLite dev) DB connection
        # between ffmpeg runs and lets polling clients see progress.
        await db.commit()

    await db.commit()
    await timeline.settle_job(db, job_id, changed)
    return {"fitted": fitted, "left": left}


@router.post("/jobs/{job_id}/segments/tidy-lanes", status_code=status.HTTP_200_OK)
async def tidy_segment_lanes(job_id: str, db: AsyncSession = Depends(get_db, scope="function")):
    """
    Repack scattered clips into the minimum number of timeline lanes:
    left-to-right greedy assignment — each clip drops into the first lane
    whose previous clip has already ended, opening a new lane only for true
    time overlaps. Non-overlapping tracks collapse back to a single lane.
    A track with a switch on (locked or muted) keeps its clips, and
    the others are packed around it.
    """
    result = await db.execute(
        select(Segment)
        .where(Segment.job_id == job_id)
        .order_by(Segment.start_time, Segment.end_time)
    )
    segments = result.scalars().all()
    if not segments:
        raise HTTPException(status_code=404, detail="No segments found for this job")
    job = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
    switched = set(timeline.flags_from(job.track_flags if job else None))

    def free_numbers():
        n = 0
        while True:
            if n not in switched:
                yield n
            n += 1

    numbers = free_numbers()
    lanes: list[int] = []         # the track numbers in use, in order
    lane_ends: dict[int, float] = {}   # last occupied end_time per track
    changed = 0
    for seg in segments:
        if (seg.lane_index or 0) in switched:
            continue
        lane = next((n for n in lanes if seg.start_time >= lane_ends[n] - 0.001), None)
        if lane is None:
            lane = next(numbers)
            lanes.append(lane)
        lane_ends[lane] = seg.end_time
        if (seg.lane_index or 0) != lane:
            seg.lane_index = lane
            changed += 1

    await db.commit()
    return {"changed": changed, "lanes": len(lanes) + len({s.lane_index or 0 for s in segments} & switched)}


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
    await _refresh_faces(db, target.project_id)   # the character now has both faces to choose from
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
    # a snapshot doesn't carry the thumbnail: both characters take their best confirmed face again
    await _refresh_faces(db, target.project_id)
    await db.refresh(speaker)
    return speaker


@router.get("/projects/{project_id}/character-suggestions", response_model=List[CharacterSuggestion])
async def character_suggestions(
    project_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Characters that may be one person: cast with the same voice by the user, or close in voice and face. Only
    suggestions — nothing is merged here; the user's click calls /speakers/{id}/merge.
    """
    from app.services.characters import suggestions
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return await suggestions.suggest(db, project_id, project.character_dismissals)


@router.post("/projects/{project_id}/character-suggestions/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss_character_suggestion(
    project_id: str,
    payload: CharacterDismiss,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Not the same: the pair (given in either order) is never suggested again."""
    from app.services.characters import suggestions
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if payload.a == payload.b:
        raise HTTPException(status_code=400, detail="Choose two different characters")
    found = (await db.execute(select(Speaker.id).where(
        Speaker.project_id == project_id, Speaker.id.in_([payload.a, payload.b])))).scalars().all()
    if len(set(found)) != 2:
        raise HTTPException(status_code=400, detail="Those characters aren't in this project")
    pairs = suggestions.dismissals_from(project.character_dismissals)
    pairs.add(suggestions.pair_of(payload.a, payload.b))
    project.character_dismissals = suggestions.dismissals_to(pairs)
    await db.commit()
