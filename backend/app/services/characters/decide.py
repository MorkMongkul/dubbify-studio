"""Who said this line? A character's profile is the mean of its confirmed lines' prints; `decide` compares one line's
voice print with every profile and says whether it is sure. Pure numpy: no database, no models.

By voice only since 0.5.2: the face step (every frame through three models) took 2–3 min per episode, and the owner
chose speed (measured on ep01–10: 74.1% right with faces). A face print an older line still has is ignored. Sure: the
voice leads the runner-up by SURE_VOICE_MARGIN — never while another line is spoken over this one (its voice print
holds both voices: `overlapping`), and never with one voice profile (no runner-up)."""
from dataclasses import dataclass

import numpy as np

from app.services.characters.types import LinePrints

SURE_VOICE_MARGIN = 0.10  # best minus second voice cosine
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
    return Profile(
        _mean_unit([p.voice for p in prints if p.voice is not None]),
        _mean_unit([p.face for p in prints if p.face is not None]),
        len(prints),
    )


def _ranked(print_: np.ndarray | None, profiles: dict[str, Profile], kind: str) -> list[tuple[str, float]]:
    """Characters with a `kind` profile, best cosine first."""
    if print_ is None:
        return []
    scored = [
        (sid, float(np.dot(print_, getattr(prof, kind))))
        for sid, prof in profiles.items()
        if getattr(prof, kind) is not None
    ]
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
    voices = _ranked(p.voice, profiles, "voice")
    if not voices or voices[0][1] < UNKNOWN_VOICE_FLOOR:
        return Decision(None, False, [(sid, "voice") for sid, _ in voices[:2]])

    voice_pick = voices[0][0]
    margin = voices[0][1] - voices[1][1] if len(voices) > 1 else None
    if margin is not None and margin >= SURE_VOICE_MARGIN - _TOLERANCE and not mixed_voice and may_be_sure:
        return Decision(voice_pick, True, [])
    return Decision(voice_pick, False, [(sid, "voice") for sid, _ in voices[:2]])
