"""
app/services/media.py
The rules an episode's media clips follow (the Media tab, 0.4.0). The editor
creates and edits clips (frontend/src/lib/media.ts) and sends the episode's
whole list; these rules keep a stored list sound and place a template's clips.

A clip shows its media asset from start_time to end_time, playing it from
source_in, in a box of the frame (fractions). A higher lane_index is drawn over
a lower one, so clips never change lane by themselves.
"""
from __future__ import annotations

import json

IMAGE_SECS = 5.0
MIN_CLIP_SECS = 0.1
MAX_EXPORT_CLIPS = 32
VOLUME_MIN_DB = -20.0
VOLUME_MAX_DB = 10.0
MIN_BOX = 0.02
FLAG_KEYS = ("locked", "hidden", "muted")
EPS = 1e-6

Flags = dict[int, dict[str, bool]]


def _get(o, key, default=None):
    return o.get(key, default) if isinstance(o, dict) else getattr(o, key, default)


def _r(x: float) -> float:
    return round(float(x), 6)


def normalize(clips: list[dict], assets: dict, episode_secs: float) -> list[dict]:
    """
    A list as stored: inside the episode, at least MIN_CLIP_SECS long, a video
    never past its own end, box and volume in range. Raises ValueError for media
    not in the library or two clips overlapping on one track.
    """
    out: list[dict] = []
    for c in clips:
        asset = assets.get(c["asset_id"])
        if asset is None:
            raise ValueError("unknown media")
        start = max(0.0, float(c["start_time"]))
        end = float(c["end_time"])
        if episode_secs > 0:
            end = min(end, episode_secs)
        source_in = max(0.0, float(c.get("source_in") or 0.0))
        if _get(asset, "kind") == "video":
            duration = float(_get(asset, "duration_secs") or 0.0)
            source_in = min(source_in, max(0.0, duration - MIN_CLIP_SECS))
            end = min(end, start + max(MIN_CLIP_SECS, duration - source_in))
        else:
            source_in = 0.0
        if end - start < MIN_CLIP_SECS:
            end = start + MIN_CLIP_SECS
            if episode_secs > 0 and end > episode_secs:
                end, start = episode_secs, max(0.0, episode_secs - MIN_CLIP_SECS)
        out.append({
            **c,
            "lane_index": max(0, int(c.get("lane_index") or 0)),
            "start_time": _r(start), "end_time": _r(end), "source_in": _r(source_in),
            "x": _r(min(5.0, max(-5.0, float(c.get("x") or 0.0)))),
            "y": _r(min(5.0, max(-5.0, float(c.get("y") or 0.0)))),
            "width": _r(min(10.0, max(MIN_BOX, float(c.get("width") or 0.0)))),
            "height": _r(min(10.0, max(MIN_BOX, float(c.get("height") or 0.0)))),
            "volume_db": _r(min(VOLUME_MAX_DB, max(VOLUME_MIN_DB, float(c.get("volume_db") or 0.0)))),
        })
    by_lane: dict[int, list[dict]] = {}
    for c in out:
        by_lane.setdefault(c["lane_index"], []).append(c)
    for lane, lane_clips in by_lane.items():
        lane_clips.sort(key=lambda c: c["start_time"])
        for a, b in zip(lane_clips, lane_clips[1:]):
            if b["start_time"] < a["end_time"] - EPS:
                raise ValueError(f"clips overlap on track {lane}")
    return out


def _free(lane: int, start: float, end: float, placed: list[dict]) -> bool:
    return all(c["lane_index"] != lane or c["end_time"] <= start + EPS or c["start_time"] >= end - EPS for c in placed)


def fit_template(items: list[dict], episode_secs: float, existing: list[dict]) -> list[dict]:
    """
    A template's clips placed on an episode: a clip past its end moves earlier so
    it ends at the end (never cut mid-animation; dropped when longer than the
    episode); each keeps its track when that's free over its span, else goes on a
    new track above everything. The episode's own clips are never touched.
    """
    placed = [dict(c) for c in existing]
    made: list[dict] = []
    for item in items:
        start, end = float(item["start_time"]), float(item["end_time"])
        length = end - start
        if length > episode_secs + EPS:
            continue
        if end > episode_secs:
            start, end = episode_secs - length, episode_secs
        lane = int(item.get("lane_index") or 0)
        if not _free(lane, start, end, placed):
            lane = max([c["lane_index"] for c in placed] + [lane]) + 1
        clip = {**item, "start_time": _r(start), "end_time": _r(end), "lane_index": lane}
        placed.append(clip)
        made.append(clip)
    return made


def flags_from(raw) -> Flags:
    """Each media track's switches from jobs.media_track_flags (JSON text, or a parsed dict)."""
    if not raw:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return {}
    out: Flags = {}
    for lane, f in (data or {}).items():
        try:
            n = int(lane)
        except (TypeError, ValueError):
            continue
        switches = {k: bool((f or {}).get(k)) for k in FLAG_KEYS}
        if any(switches.values()):
            out[n] = switches
    return out


def flags_to(flags: Flags) -> str | None:
    on = {str(lane): f for lane, f in sorted(flags.items()) if any(f.get(k) for k in FLAG_KEYS)}
    return json.dumps(on) if on else None


def prune_flags(flags: Flags, clips) -> Flags:
    """Only media tracks that still have clips keep their switches."""
    used = {int(_get(c, "lane_index") or 0) for c in clips}
    return {lane: f for lane, f in flags.items() if lane in used}
