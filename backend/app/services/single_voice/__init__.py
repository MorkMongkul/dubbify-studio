"""Single-voice dub (0.5.7, docs/superpowers/specs/2026-11-05-single-voice-dub-design.md): a project kind whose own
pipeline transcribes on the Mac, translates every line as free, natural spoken Khmer, voices them all with one Edge TTS
voice and exports — with no speaker detection, recognition or casting. Normal projects never reach this package; the
only hooks in existing code route a single-voice project here (end of Stage 1, Analyze, a line's voice)."""

SINGLE_VOICE = "single_voice"
VOICES = {"km-KH-PisethNeural": "Piseth (man)", "km-KH-SreymomNeural": "Sreymom (woman)"}
DEFAULT_VOICE = "km-KH-PisethNeural"
RATE_MIN, RATE_MAX = -50, 50   # percent, Edge's own speaking rate


def is_single_voice(project) -> bool:
    return project is not None and getattr(project, "dub_mode", None) == SINGLE_VOICE
