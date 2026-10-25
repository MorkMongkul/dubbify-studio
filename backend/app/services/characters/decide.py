"""Who said this line? A character's profile is the mean of its confirmed lines' prints; `decide` compares one line's
prints with every profile and says whether it is sure. Pure numpy: no database, no models."""
from dataclasses import dataclass

import numpy as np

from app.services.characters.types import LinePrints

SURE_VOICE_MARGIN = 0.10  # best minus second voice cosine, for a line with no speaking face
# Both floors were measured on the owner's project and kept: from 0.25 to 0.45 (voice) and 0.25 to 0.6 (face) they
# moved at most 3 of 169 lines. A new actor's best voice cosine (0.42-0.58) sits among the known actors' (0.47 and
# up on 95% of lines), so the voice floor only catches a voice far from everyone.
UNKNOWN_VOICE_FLOOR = 0.35  # below this, no voice profile is the speaker
UNKNOWN_FACE_FLOOR = 0.363  # OpenCV's published SFace same-person cosine threshold
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
    guesses: list[tuple[str, str]]  # at most 2 of (speaker_id, "voice + face" | "face" | "voice")


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


def decide(p: LinePrints, profiles: dict[str, Profile]) -> Decision:
    voices = _ranked(p.voice, profiles, "voice")
    faces = [s for s in _ranked(p.face, profiles, "face") if s[1] >= UNKNOWN_FACE_FLOOR]
    voice_pick = voices[0][0] if voices else None
    face_pick = faces[0][0] if faces else None

    if face_pick is not None:
        if voice_pick == face_pick:
            return Decision(face_pick, True, [])
        if voice_pick is not None:
            return Decision(face_pick, False, [(face_pick, "face"), (voice_pick, "voice")])
        return Decision(face_pick, False, [(sid, "face") for sid, _ in faces[:2]])

    if not voices or voices[0][1] < UNKNOWN_VOICE_FLOOR:
        return Decision(None, False, [(sid, "voice") for sid, _ in voices[:2]])

    margin = voices[0][1] - voices[1][1] if len(voices) > 1 else None
    if margin is not None and margin >= SURE_VOICE_MARGIN - _TOLERANCE:
        return Decision(voice_pick, True, [])
    return Decision(voice_pick, False, [(sid, "voice") for sid, _ in voices[:2]])
