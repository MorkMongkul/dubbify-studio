"""Same character? Two labels that look like one character are offered to the user, never merged: the Speakers
card's *Same person as…* does the work, so a merge stays one click and one undo step.

A pair is suggested when both characters were cast with the same voice by a person (an app-picked voice repeats
across episodes, so it says nothing), or when their voice AND face profiles are close — never when a person cast
them with different voices (one voice is one actor). A pair the user said is not
the same is kept in `projects.character_dismissals` (a JSON list of sorted [a, b] pairs) and never comes back."""
import json
from itertools import combinations
from pathlib import Path

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.models import Job, LinePrint, Segment, Speaker
from app.services.characters import store
from app.services.characters.decide import Profile
from app.services.characters.store import project_profiles
from app.services.timeline import actor_span

# Measured on the owner's project after the upgrade: voice profiles of different actors reach 0.89, so the face
# decides; pairs cast with different voices reached 0.74 in face (labels that hold two actors), one actor's two
# labels 0.85. At 0.6 three of eleven suggestions were such mixed pairs, which a Merge would get wrong.
SAME_VOICE = 0.75  # voice profiles' cosine at or above this
SAME_FACE = 0.75  # face profiles' cosine at or above this (was 0.6)
SAMPLE_SECS = 6.0  # ▶ plays at most this much of a character's line

Pair = tuple[str, str]


def pair_of(a: str, b: str) -> Pair:
    return (a, b) if a < b else (b, a)


def dismissals_from(raw: str | None) -> set[Pair]:
    """projects.character_dismissals as a set of sorted pairs (a damaged value counts as none)."""
    try:
        return {pair_of(a, b) for a, b in json.loads(raw)} if raw else set()
    except (ValueError, TypeError):
        return set()


def dismissals_to(pairs: set[Pair]) -> str | None:
    return json.dumps([list(p) for p in sorted(pairs)]) if pairs else None


def _cosine(a: np.ndarray | None, b: np.ndarray | None) -> float | None:
    return None if a is None or b is None else float(np.dot(a, b))


def _cast_apart(x: Speaker, y: Speaker) -> bool:
    """A person cast them with different voices: one voice is one actor for the whole series (Ruling AO), so they
    are different people however close their profiles are. A voice "Choose for me" picked says nothing."""
    return bool(x.voice_id and y.voice_id and x.voice_id != y.voice_id and not x.voice_auto and not y.voice_auto)


def find(speakers: list[Speaker], profiles: dict[str, Profile], dismissed: set[Pair],
         lines: dict[str, int] | None = None) -> list[dict]:
    """[{a, b, reason, a_lines, b_lines}] with a < b (the lines each has in the whole project): "same voice" first, then the voice cosine falling, then by pair."""
    found: list[tuple[int, float, Pair, str]] = []
    for x, y in combinations(sorted(speakers, key=lambda s: s.id), 2):
        pair = (x.id, y.id)
        if pair in dismissed:
            continue
        px, py = profiles.get(x.id), profiles.get(y.id)
        voice = _cosine(px and px.voice, py and py.voice)
        face = _cosine(px and px.face, py and py.face)
        if (voice is not None and voice >= SAME_VOICE and face is not None and face >= SAME_FACE
                and not _cast_apart(x, y)):
            found.append((1, voice, pair, "same voice and face"))
        elif (x.voice_id and x.voice_id == y.voice_id and not x.voice_auto and not y.voice_auto):
            found.append((0, voice if voice is not None else 0.0, pair, "same voice"))
    found.sort(key=lambda f: (f[0], -f[1], f[2]))
    lines = lines or {}
    return [{"a": pair[0], "b": pair[1], "reason": reason, "a_lines": lines.get(pair[0], 0), "b_lines": lines.get(pair[1], 0)}
            for _, _, pair, reason in found]


def _voices_file(job: Job) -> Path | None:
    """The episode's separated voices as the preview plays them (the .m4a), else the .wav; None before separation."""
    folder = store.vocals_path(job).parent
    return next((f for f in (folder / "vocals.m4a", folder / "vocals.wav") if f.exists()), None)


async def samples(db: AsyncSession, speaker_ids: list[str]) -> dict[str, dict]:
    """A line of each character to hear in the original, from ANY episode (the one open may not have them): their
    confirmed lines first, the one a face was seen speaking most surely, then the longest; where the actor spoke, at
    most SAMPLE_SECS. Characters with no line in a separated episode get none."""
    rows = (await db.execute(
        select(Segment, Job, LinePrint.speaking_score)
        .join(Job, Job.id == Segment.job_id)
        .outerjoin(LinePrint, LinePrint.segment_id == Segment.id)
        .where(Segment.speaker_id.in_(speaker_ids)))).all()
    files: dict[str, Path | None] = {}
    best: dict[str, tuple] = {}
    for seg, job, score in rows:
        if job.id not in files:
            files[job.id] = _voices_file(job)
        start, end = actor_span(seg)
        if files[job.id] is None or end <= start:
            continue
        key = (store.is_confirmed(seg, job), float("-inf") if score is None else score, end - start)
        if seg.speaker_id not in best or key > best[seg.speaker_id][0]:
            best[seg.speaker_id] = (key, files[job.id], start, end)
    out = {}
    for speaker_id, (_, file, start, end) in best.items():
        url = f"/uploads/{file.relative_to(Path(settings.UPLOAD_DIR)).as_posix()}?v={int(file.stat().st_mtime)}"
        out[speaker_id] = {"url": url, "start": start, "end": min(end, start + SAMPLE_SECS)}
    return out


async def suggest(db: AsyncSession, project_id: str, dismissed_raw: str | None) -> list[dict]:
    """Read only: nothing is written or merged here."""
    speakers = (await db.execute(select(Speaker).where(Speaker.project_id == project_id))).scalars().all()
    # one grouped count for the whole project (every episode), not one query per character
    counts = (await db.execute(
        select(Segment.speaker_id, func.count(Segment.id)).where(Segment.speaker_id.in_([x.id for x in speakers]))
        .group_by(Segment.speaker_id))).all()
    found = find(list(speakers), await project_profiles(db, project_id), dismissals_from(dismissed_raw),
                 {sid: n for sid, n in counts})
    if found:
        heard = await samples(db, sorted({pair[k] for pair in found for k in ("a", "b")}))
        for pair in found:
            pair["a_sample"], pair["b_sample"] = heard.get(pair["a"]), heard.get(pair["b"])
    return found
