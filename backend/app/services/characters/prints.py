"""Prints for a line: the voice print of its vocals (characters.voiceprint). By voice only since 0.5.2: the face step
(every frame through YuNet, Light-ASD and SFace) took 2–3 min per episode; a face an older line was given stays with it
(store.save_prints).

prints_for_spans is blocking (ffmpeg, sherpa-onnx): call it through asyncio.to_thread."""
from app.services.characters import voiceprint
from app.services.characters.types import LinePrints

__all__ = ["LinePrints", "prints_for_spans"]


def prints_for_spans(video_path: str, vocals_path: str | None, spans: list[tuple[float, float]]) -> list[LinePrints]:
    """Prints for each (start, end) span, in seconds, of the vocals (the video isn't read; it stays in the signature
    the callers share). Without vocals there is no voice to print."""
    if vocals_path is None:
        return [LinePrints(None, None, None, None) for _ in spans]
    result = []
    for start, end in spans:
        start = max(start, 0.0)
        if end <= start:
            result.append(LinePrints(None, None, None, None))
            continue
        result.append(LinePrints(voiceprint.voice_print(voiceprint.read_audio(vocals_path, start, end)), None, None, None))
    return result
