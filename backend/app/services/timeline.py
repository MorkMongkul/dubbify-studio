"""
app/services/timeline.py
The timeline's timing rule (0.3.0), shared by everything that places a clip:
analysis, the upgrade of older episodes, edits, voice generation, Auto-fit and
the export.

A clip with a voice plays its voice file from `voice_offset` (seconds into the
file) for `end_time - start_time` seconds at `fit_rate` (on top of the speed
already baked into the file, `voice_speed`), starting at `start_time` — so it
is exactly as long as the sound it plays, and nothing changes its speed unless
the user or Auto-fit sets it. A clip with no voice yet is as long as its Khmer
text takes to say (`estimate_secs`).

Clips never overlap on one track: a clip that grows into another one moves to
the nearest track where it fits (`settle`), never in time.

The editor applies the same rules (frontend/src/lib/clipRules.ts, tracks.ts);
keep the two in lockstep — frontend/tests/clipRules.test.ts reads the
constants below from this file.
"""
from __future__ import annotations

import asyncio
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Measured 2026-10-23 on the team's voices made since 0.2.8 (silence trimmed,
# speed 1.0): 0.065 s per character of the Khmer text, median error 9%.
PACE_SECS_PER_CHAR = 0.065
MIN_CLIP_SECS = 0.1
# Auto-fit speeds a voice up at most to this (a natural-sounding cap)
AUTOFIT_FASTEST = 1.25
EPS = 0.001

# Where a phrase may be broken when a line is split
_BREAKS = set(" ។៕!?,.…，。！？、")


def estimate_secs(text: str) -> float:
    """How long a line's Khmer text takes to say, at the measured pace."""
    return max(MIN_CLIP_SECS, round(len((text or "").strip()) * PACE_SECS_PER_CHAR, 3))


def is_voiced(seg) -> bool:
    return bool(seg.tts_audio_path) and (seg.tts_duration_secs or 0) > 0


def offset_of(seg) -> float:
    return 0.0 if seg.voice_offset is None else float(seg.voice_offset)


def rate_of(seg) -> float:
    return 1.0 if not seg.fit_rate else float(seg.fit_rate)


def total_speed(seg) -> float:
    """The speed the clip's voice is heard at: the file's own speed × fit_rate."""
    return (seg.voice_speed or 1.0) * rate_of(seg)


def max_end(seg) -> float:
    """The latest a voiced clip can end: where its voice runs out."""
    return seg.start_time + ((seg.tts_duration_secs or 0.0) - offset_of(seg)) / rate_of(seg)


def clamp_voiced(seg) -> None:
    """Keep a voiced clip inside its voice and at least MIN_CLIP_SECS long."""
    tts = seg.tts_duration_secs or 0.0
    rate = rate_of(seg)
    seg.voice_offset = max(0.0, min(offset_of(seg), tts - MIN_CLIP_SECS * rate))
    seg.end_time = max(seg.start_time + MIN_CLIP_SECS, min(seg.end_time, max_end(seg)))


def follow_text(seg) -> bool:
    """A voiceless line is as long as its text takes to say. Returns whether it changed.
    A line with no text keeps its length (nothing to measure)."""
    if is_voiced(seg) or not (seg.khmer_text or "").strip():
        return False
    end = round(seg.start_time + estimate_secs(seg.khmer_text), 3)
    if abs(end - seg.end_time) <= EPS:
        return False
    seg.end_time = end
    return True


def actor_span(seg) -> tuple[float, float]:
    """Where the actor spoke in the original; the clip itself for a line added by hand."""
    if seg.source_start is not None and seg.source_end is not None:
        return seg.source_start, seg.source_end
    return seg.start_time, seg.end_time


def lane_of(seg) -> int:
    return seg.lane_index or 0


def overlaps(a, b) -> bool:
    return a.start_time < b.end_time - EPS and b.start_time < a.end_time - EPS


def free_lane(segs, seg) -> int:
    """The track `seg` should be on: its own if nothing there overlaps it, else
    the existing track nearest to it where it fits (a tie goes to the lower
    track), else a new track below the last."""
    others = [s for s in segs if s.id != seg.id]
    own = lane_of(seg)

    def fits(lane: int) -> bool:
        return not any(lane_of(s) == lane and overlaps(s, seg) for s in others)

    if fits(own):
        return own
    lanes = sorted({lane_of(s) for s in others} | {own})
    candidates = [lane for lane in lanes if lane != own and fits(lane)]
    if candidates:
        return min(candidates, key=lambda lane: (abs(lane - own), -lane))
    return max(lanes) + 1


def settle(segs, changed_ids: Iterable[str]) -> set[str]:
    """Move each changed clip that overlaps another on its track to the nearest
    track where it fits. Never touches times. Returns the ids it moved."""
    changed = set(changed_ids)
    moved: set[str] = set()
    for seg in sorted((s for s in segs if s.id in changed), key=lambda s: (s.start_time, s.end_time)):
        lane = free_lane(segs, seg)
        if lane != lane_of(seg):
            seg.lane_index = lane
            moved.add(seg.id)
    return moved


def overrun_target(seg, segs) -> float | None:
    """Where a voice should end so it doesn't run into a later line — or None
    when it doesn't. The next line is the earliest one starting after this one
    on any track; an overlap the original already had (up to where the actor
    stopped speaking) doesn't count."""
    later = [s.start_time for s in segs if s.id != seg.id and s.start_time > seg.start_time + EPS]
    if not later:
        return None
    target = min(later)
    if seg.source_end is not None:
        target = max(target, seg.source_end)
    return target if seg.end_time > target + EPS else None


def rescale_for_speed(seg, new_speed: float) -> None:
    """A new speed for the voice file: keep the same words cut. The caller
    re-renders the file, sets tts_duration_secs, then clamp_voiced."""
    old_vs = seg.voice_speed or 1.0
    old_total = total_speed(seg)
    seg.voice_offset = offset_of(seg) * old_vs / new_speed
    seg.end_time = seg.start_time + (seg.end_time - seg.start_time) * old_total / new_speed
    seg.voice_speed = new_speed
    seg.fit_rate = 1.0


def split_text(text: str, share: float) -> tuple[str, str]:
    """Split a line's text at the phrase break (a space or punctuation) nearest
    to `share` of it. With no break, both halves keep the whole text."""
    text = text or ""
    candidates = [i + 1 for i, ch in enumerate(text) if ch in _BREAKS and 0 < i + 1 < len(text)]
    if not candidates:
        return text, text
    target = round(share * len(text))
    b = min(candidates, key=lambda i: abs(i - target))
    left, right = text[:b].strip(), text[b:].strip()
    if not left or not right:
        return text, text
    return left, right


# ── Settling a job's tracks after a change ────────────────────

_locks: dict[str, asyncio.Lock] = {}


def job_lock(job_id: str) -> asyncio.Lock:
    """One lock per job: settling reads fresh rows under it, so writers that
    settle concurrently (batch voice generation) can't leave two clips
    overlapping on one track."""
    lock = _locks.get(job_id)
    if lock is None:
        lock = _locks[job_id] = asyncio.Lock()
    return lock


async def settle_job(db: AsyncSession, job_id: str, changed_ids: Iterable[str]) -> set[str]:
    """Settle a job's tracks after a change the caller has already committed
    (anything not committed in this session's segments is overwritten).
    Returns the ids of the clips it moved to another track."""
    from app.models.models import Segment

    changed = list(changed_ids)
    if not changed:
        return set()
    async with job_lock(job_id):
        # Fresh rows (another writer may have committed since this session read
        # them), refreshing only the segments so the caller's other objects stay usable
        segs = (await db.execute(
            select(Segment).where(Segment.job_id == job_id).execution_options(populate_existing=True)
        )).scalars().all()
        moved = settle(segs, changed)
        await db.commit()
    return moved


# ── Episodes from before 0.3.0, and freshly analysed lines ────

# The old app sped a voice up to fit its line (preview and export alike) when
# no speed was set and the voice was more than this much longer than the line
OLD_FIT_THRESHOLD = 1.05
# The fastest a converted voice keeps sounding: the most a clip render allows
OLD_FIT_FASTEST = 2.0


def convert_job(segs) -> None:
    """Give an episode from before 0.3.0 the new timing without changing how
    it sounds. The old span becomes where the actor spoke; a voice the old app
    sped up to fit keeps that speed as its visible setting (up to 2×); a voice
    shorter than its line ends where it ends; a line with no voice takes its
    text's length."""
    for seg in segs:
        seg.source_start, seg.source_end = seg.start_time, seg.end_time
        if is_voiced(seg):
            tts = seg.tts_duration_secs
            ratio = tts / max(MIN_CLIP_SECS, seg.end_time - seg.start_time)
            manual = abs((seg.voice_speed or 1.0) - 1.0) > 0.001
            seg.voice_offset = 0.0
            seg.fit_rate = min(OLD_FIT_FASTEST, ratio) if not manual and ratio > OLD_FIT_THRESHOLD else 1.0
            seg.end_time = seg.start_time + tts / seg.fit_rate
            seg.voice_text = seg.khmer_text
        else:
            follow_text(seg)


def _ensure_ids(segs) -> None:
    from app.models.models import generate_uuid
    for seg in segs:
        if not seg.id:
            seg.id = generate_uuid()


def lay_out_new(segs) -> None:
    """Freshly analysed lines (not saved yet): each is as long as its text,
    and none overlaps another on its track."""
    _ensure_ids(segs)
    for seg in segs:
        follow_text(seg)
    settle(segs, [s.id for s in segs])


async def convert_old_jobs() -> int:
    """Convert every episode from before 0.3.0 (timeline_version NULL), one
    commit per episode, so a restart part-way never converts one twice.
    Returns how many were converted."""
    import app.core.database as core_db
    from app.models.models import Job, Segment

    async with core_db.AsyncSessionLocal() as db:
        job_ids = (await db.execute(select(Job.id).where(Job.timeline_version.is_(None)))).scalars().all()
    converted = 0
    for job_id in job_ids:
        async with job_lock(job_id):
            async with core_db.AsyncSessionLocal() as db:
                job = await db.get(Job, job_id)
                if job is None or job.timeline_version is not None:
                    continue
                segs = (await db.execute(select(Segment).where(Segment.job_id == job_id))).scalars().all()
                convert_job(segs)
                settle(segs, [s.id for s in segs])
                job.timeline_version = 1
                await db.commit()
                converted += 1
    return converted
