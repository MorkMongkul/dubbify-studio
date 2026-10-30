"""Who said this line? A character's profile is its confirmed lines' prints: their mean, and every voice print; `decide`
compares one line's voice print with every profile and says whether it is sure. Pure numpy: no database, no models.

A character's score (0.5.3) blends the mean of the line's TOP_K closest checked prints of theirs with the cosine to
their mean (VOICE_BLEND): an actor heard calm and shouting has two kinds of print, and the mean sits between them.
Measured on the owner's 30 finished episodes as it happens: 63.1% → 65.7% right with the ERes2NetV2 prints alone,
67.8% with CAM++ added to the print (characters.voiceprint); sure on 35.8% at 85.0% (was 36.2% at 84.6%). Tried and
dropped: lines of an episode that sound alike sharing their scores, and a man/woman tie-breaker (worth +5 points only
with a gender always right; at 90% right it gained nothing or lost).

By voice only since 0.5.2: the face step (every frame through three models) took 2–3 min per episode, and the owner
chose speed (measured on ep01–10: 74.1% right with faces). A face print an older line still has is ignored. Sure: the
voice leads the runner-up by SURE_VOICE_MARGIN — never while another line is spoken over this one (its voice print
holds both voices: `overlapping`), and never with one voice profile (no runner-up)."""
from dataclasses import dataclass

import numpy as np

from app.services.characters.types import LinePrints

SURE_VOICE_MARGIN = 0.10  # best minus second voice score (0.08: sure on 44.5% at 82.8%; 0.12: 29.4% at 88.2%)
TOP_K = 5                 # the line's closest checked prints of a character...
VOICE_BLEND = 0.5         # ...averaged, and blended half and half with the cosine to the character's mean
# Measured on the owner's project and kept: from 0.25 to 0.45 it moved at most 3 of 169 lines. A new actor's best voice
# cosine (0.42-0.58) sits among the known actors' (0.47 and up on 95% of lines), so the floor only catches a voice far
# from everyone.
UNKNOWN_VOICE_FLOOR = 0.35  # below this, no voice profile is the speaker
_TOLERANCE = 1e-9  # so a margin of "exactly" 0.10 isn't lost to float rounding


@dataclass
class Profile:
    voice: np.ndarray | None
    face: np.ndarray | None
    lines: int
    voices: np.ndarray | None = None   # every confirmed line's voice print, one per row (None: score by the mean only)


@dataclass
class Decision:
    speaker_id: str | None
    sure: bool
    guesses: list[tuple[str, str]]  # at most 2 of (speaker_id, "voice"); lines from 0.5.0/0.5.1 may also hold "face"


def _mean_unit(vectors: list[np.ndarray]) -> np.ndarray | None:
    if not vectors:
        return None
    mean = np.mean(vectors, axis=0)
    norm = np.linalg.norm(mean)
    return (mean / norm).astype(np.float32) if norm > 0 else None


def profile_from(prints: list[LinePrints]) -> Profile:
    voices = [p.voice for p in prints if p.voice is not None]
    return Profile(
        _mean_unit(voices),
        _mean_unit([p.face for p in prints if p.face is not None]),
        len(prints),
        np.asarray(voices, np.float32) if voices else None,
    )


def _voice_score(voice: np.ndarray, profile: Profile) -> float:
    mean = float(np.dot(voice, profile.voice))
    if profile.voices is None or not len(profile.voices):
        return mean
    closest = np.sort(profile.voices @ voice)[::-1][:TOP_K]
    return VOICE_BLEND * float(np.mean(closest)) + (1 - VOICE_BLEND) * mean


def _ranked(voice: np.ndarray | None, profiles: dict[str, Profile]) -> list[tuple[str, float]]:
    """Characters with a voice profile, best score first."""
    if voice is None:
        return []
    scored = [(sid, _voice_score(voice, prof)) for sid, prof in profiles.items() if prof.voice is not None]
    return sorted(scored, key=lambda s: -s[1])


def overlapping(spans: list[tuple[float, float]]) -> list[bool]:
    """Per span (where a line's actor spoke), whether another line's span overlaps it — touching isn't overlapping.
    Those lines' voice prints are mixed."""
    return [any(min(end, other_end) > max(start, other_start)
                for k, (other_start, other_end) in enumerate(spans) if k != i)
            for i, (start, end) in enumerate(spans)]


def decide(p: LinePrints, profiles: dict[str, Profile], mixed_voice: bool = False,
           may_be_sure: bool = True) -> Decision:
    """mixed_voice: another line is spoken over this one (`overlapping`), so its voice print holds two voices.
    may_be_sure: False while the profiles are incomplete (confirmed lines still wait for their prints): a line that
    would be sure is then to check, its pick the first guess."""
    voices = _ranked(p.voice, profiles)
    if not voices or voices[0][1] < UNKNOWN_VOICE_FLOOR:
        return Decision(None, False, [(sid, "voice") for sid, _ in voices[:2]])

    voice_pick = voices[0][0]
    margin = voices[0][1] - voices[1][1] if len(voices) > 1 else None
    if margin is not None and margin >= SURE_VOICE_MARGIN - _TOLERANCE and not mixed_voice and may_be_sure:
        return Decision(voice_pick, True, [])
    return Decision(voice_pick, False, [(sid, "voice") for sid, _ in voices[:2]])
