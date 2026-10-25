"""Same character? Two labels that look like one character are offered to the user, never merged: the Speakers
card's *Same person as…* does the work, so a merge stays one click and one undo step.

A pair is suggested when both characters were cast with the same voice by a person (an app-picked voice repeats
across episodes, so it says nothing), or when their voice AND face profiles are close. A pair the user said is not
the same is kept in `projects.character_dismissals` (a JSON list of sorted [a, b] pairs) and never comes back."""
import json
from itertools import combinations

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Speaker
from app.services.characters.decide import Profile
from app.services.characters.store import project_profiles

SAME_VOICE = 0.75  # voice profiles' cosine at or above this (Task 14 tunes it)
SAME_FACE = 0.6  # face profiles' cosine at or above this (Task 14 tunes it)

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


def find(speakers: list[Speaker], profiles: dict[str, Profile], dismissed: set[Pair]) -> list[dict]:
    """[{a, b, reason}] with a < b: "same voice" first, then the voice cosine falling, then by pair."""
    found: list[tuple[int, float, Pair, str]] = []
    for x, y in combinations(sorted(speakers, key=lambda s: s.id), 2):
        pair = (x.id, y.id)
        if pair in dismissed:
            continue
        px, py = profiles.get(x.id), profiles.get(y.id)
        voice = _cosine(px and px.voice, py and py.voice)
        face = _cosine(px and px.face, py and py.face)
        if voice is not None and voice >= SAME_VOICE and face is not None and face >= SAME_FACE:
            found.append((1, voice, pair, "same voice and face"))
        elif (x.voice_id and x.voice_id == y.voice_id and not x.voice_auto and not y.voice_auto):
            found.append((0, voice if voice is not None else 0.0, pair, "same voice"))
    found.sort(key=lambda f: (f[0], -f[1], f[2]))
    return [{"a": pair[0], "b": pair[1], "reason": reason} for _, _, pair, reason in found]


async def suggest(db: AsyncSession, project_id: str, dismissed_raw: str | None) -> list[dict]:
    """Read only: nothing is written or merged here."""
    speakers = (await db.execute(select(Speaker).where(Speaker.project_id == project_id))).scalars().all()
    return find(list(speakers), await project_profiles(db, project_id), dismissals_from(dismissed_raw))
