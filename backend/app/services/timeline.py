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
import json
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
# 0.3.1: the music & effects dip this much under the voices, with ramps this long
DUCK_DB = -8.0
DUCK_RAMP_SECS = 0.2
# The volume a clip can be given (the transcript panel's and the volume line's range)
VOLUME_MIN_DB = -20.0
VOLUME_MAX_DB = 10.0

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
    clamp_fades(seg)


def follow_text(seg) -> bool:
    """A voiceless line is as long as its text takes to say. Returns whether it changed.
    A line with no text keeps its length (nothing to measure)."""
    if is_voiced(seg) or not (seg.khmer_text or "").strip():
        return False
    end = round(seg.start_time + estimate_secs(seg.khmer_text), 3)
    if abs(end - seg.end_time) <= EPS:
        return False
    seg.end_time = end
    clamp_fades(seg)
    return True


# ── Fades (0.3.1) ──────────────────────────────────────────────

def fades_of(seg) -> tuple[float, float]:
    """The clip's fade-in and fade-out, in seconds of timeline (NULL: none)."""
    return (getattr(seg, "fade_in_secs", None) or 0.0, getattr(seg, "fade_out_secs", None) or 0.0)


def clamp_fades(seg) -> None:
    """Fades are never negative and never longer, together, than the clip:
    when they are, both shrink by the same factor."""
    fade_in, fade_out = (max(0.0, f) for f in fades_of(seg))
    length = max(0.0, seg.end_time - seg.start_time)
    if fade_in + fade_out > length + EPS:
        k = length / (fade_in + fade_out)
        fade_in, fade_out = fade_in * k, fade_out * k
    seg.fade_in_secs, seg.fade_out_secs = round(fade_in, 3), round(fade_out, 3)


def fade_gain_at(seg, t: float) -> float:
    """The clip's fade gain at timeline time `t` (linear, like ffmpeg's afade)."""
    if t < seg.start_time or t >= seg.end_time:
        return 0.0
    fade_in, fade_out = fades_of(seg)
    gain = 1.0
    if fade_in > 0:
        gain = min(gain, (t - seg.start_time) / fade_in)
    if fade_out > 0:
        gain = min(gain, (seg.end_time - t) / fade_out)
    return max(0.0, gain)


# ── The music dips under the voices (0.3.1) ────────────────────

def duck_spans(segs) -> list[tuple[float, float]]:
    """Where voices play, merged when two are closer than two ramps (so the
    music doesn't come up for an instant between them)."""
    spans: list[list[float]] = []
    for a, b in sorted((s.start_time, s.end_time) for s in segs if is_voiced(s)):
        if spans and a - spans[-1][1] < 2 * DUCK_RAMP_SECS:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in spans]


def _duck_floor() -> float:
    return 10 ** (DUCK_DB / 20)


def duck_gain_at(spans, t: float) -> float:
    """The music's gain at `t`: down by DUCK_DB under a voice, ramping over
    DUCK_RAMP_SECS before it starts and after it ends."""
    r = DUCK_RAMP_SECS
    depth = 0.0
    for a, b in spans:
        up = min(1.0, max(0.0, (t - (a - r)) / r))
        down = min(1.0, max(0.0, ((b + r) - t) / r))
        depth = max(depth, up * down)
    return 1 - (1 - _duck_floor()) * depth


def duck_volume_expr(spans) -> str:
    """duck_gain_at as an ffmpeg `volume` expression over `t` (the spans are
    merged, so their ramps never overlap and a sum is their max)."""
    if not spans:
        return "1"
    r = DUCK_RAMP_SECS
    terms = "+".join(
        f"clip((t-{a - r:.3f})/{r},0,1)*clip(({b + r:.3f}-t)/{r},0,1)" for a, b in spans
    )
    return f"1-{1 - _duck_floor():.6f}*({terms})"


def actor_span(seg) -> tuple[float, float]:
    """Where the actor spoke in the original; the clip itself for a line added by hand."""
    if seg.source_start is not None and seg.source_end is not None:
        return seg.source_start, seg.source_end
    return seg.start_time, seg.end_time


def lane_of(seg) -> int:
    return seg.lane_index or 0


def overlaps(a, b) -> bool:
    return a.start_time < b.end_time - EPS and b.start_time < a.end_time - EPS


# ── Track switches: lock, hide, mute (like CapCut's track headers) ──
# Stored per episode in jobs.track_flags as JSON, keyed by lane_index; only
# tracks with a switch on. Locked: no edits (the editor enforces it) and the
# server never moves another clip onto it. Hidden: its subtitles aren't shown
# or exported. Muted: its voices aren't played or exported (export_lines).

FLAG_KEYS = ("locked", "hidden", "muted")
Flags = dict[int, dict[str, bool]]


def flags_from(raw) -> Flags:
    """A job's switches from jobs.track_flags (JSON text, or an already parsed dict)."""
    if not raw:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return {}
    out: Flags = {}
    for lane, f in (data or {}).items():
        try:
            lane_no = int(lane)
        except (TypeError, ValueError):
            continue
        f = f if isinstance(f, dict) else getattr(f, "model_dump", lambda: {})()
        on = {k: bool(f.get(k)) for k in FLAG_KEYS}
        if lane_no >= 0 and any(on.values()):
            out[lane_no] = on
    return out


def flags_to(flags: Flags) -> str | None:
    on = {str(lane): f for lane, f in sorted(flags.items()) if any(f.get(k) for k in FLAG_KEYS)}
    return json.dumps(on) if on else None


def flag(flags: Flags | None, lane: int, key: str) -> bool:
    return bool(flags and flags.get(lane, {}).get(key))


def prune_flags(flags: Flags, segs) -> Flags:
    """Only tracks that still have clips keep their switches (an empty track
    disappears, and a later clip landing on its number must not inherit them)."""
    used = {lane_of(s) for s in segs}
    return {lane: f for lane, f in flags.items() if lane in used}


def export_lines(segs, flags: Flags | None):
    """What the export plays and shows: the voices of tracks that aren't muted,
    and the subtitles of tracks that aren't hidden."""
    voices = [s for s in segs if not flag(flags, lane_of(s), "muted")]
    subtitles = [s for s in segs if not flag(flags, lane_of(s), "hidden")]
    return voices, subtitles


def free_lane(segs, seg, flags: Flags | None = None) -> int:
    """The track `seg` should be on: its own if nothing there overlaps it, else
    the existing track nearest to it where it fits (a tie goes to the lower
    track) — never a locked one, and only one muted and hidden the way its own
    is — else a new track below the last."""
    others = [s for s in segs if s.id != seg.id]
    own = lane_of(seg)
    kind = (flag(flags, own, "hidden"), flag(flags, own, "muted"))

    def fits(lane: int) -> bool:
        return not any(lane_of(s) == lane and overlaps(s, seg) for s in others)

    if fits(own):
        return own
    lanes = sorted({lane_of(s) for s in others} | {own})
    candidates = [
        lane for lane in lanes
        if lane != own and fits(lane) and not flag(flags, lane, "locked")
        and (flag(flags, lane, "hidden"), flag(flags, lane, "muted")) == kind
    ]
    if candidates:
        return min(candidates, key=lambda lane: (abs(lane - own), -lane))
    return max(lanes + list((flags or {}).keys())) + 1


def settle(segs, changed_ids: Iterable[str], flags: Flags | None = None) -> set[str]:
    """Move each changed clip that overlaps another on its track to the nearest
    track where it fits. Never touches times. A new track a clip moves to is
    muted and hidden like the one it left (`flags` is updated). Returns the ids
    it moved."""
    changed = set(changed_ids)
    moved: set[str] = set()
    for seg in sorted((s for s in segs if s.id in changed), key=lambda s: (s.start_time, s.end_time)):
        own = lane_of(seg)
        lane = free_lane(segs, seg, flags)
        if lane != own:
            if flags is not None and lane not in flags and (flag(flags, own, "hidden") or flag(flags, own, "muted")):
                flags[lane] = {"locked": False, "hidden": flag(flags, own, "hidden"), "muted": flag(flags, own, "muted")}
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
    # Fades stay on the same words
    fade_in, fade_out = fades_of(seg)
    seg.fade_in_secs = round(fade_in * old_total / new_speed, 3)
    seg.fade_out_secs = round(fade_out * old_total / new_speed, 3)
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


async def after_new_voice(db: AsyncSession, seg, duration: float, spoken_text: str) -> None:
    """A new voice sets its clip: it starts where the line starts and is as
    long as the voice, which plays whole at its own speed. The caller commits,
    then settles the job (the clip may now run into the next one)."""
    seg.voice_offset = 0.0
    seg.fit_rate = 1.0
    seg.end_time = seg.start_time + duration
    seg.voice_text = spoken_text
    clamp_fades(seg)


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
    from app.models.models import Job, Segment

    changed = list(changed_ids)
    if not changed:
        return set()
    async with job_lock(job_id):
        # Fresh rows (another writer may have committed since this session read
        # them), refreshing only the segments so the caller's other objects stay usable
        segs = (await db.execute(
            select(Segment).where(Segment.job_id == job_id).execution_options(populate_existing=True)
        )).scalars().all()
        job = (await db.execute(
            select(Job).where(Job.id == job_id).execution_options(populate_existing=True)
        )).scalar_one_or_none()
        flags = flags_from(job.track_flags) if job else {}
        moved = settle(segs, changed, flags)
        if job:
            stored = flags_to(prune_flags(flags, segs))
            if stored != job.track_flags:
                job.track_flags = stored
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
