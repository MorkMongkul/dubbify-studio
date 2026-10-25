"""Prints for a line: the voice print of its vocals and, when a face on screen speaks it, that face's print.

prints_for_spans is blocking (ffmpeg, onnxruntime, sherpa-onnx): call it through asyncio.to_thread. Per line the picture
is read a frame at a time; each frame's faces give their Light-ASD crops (and every 6th frame of a track its SFace
feature) at once, and the frame goes. Each face followed for >= 10 frames is then judged by lip-sync against the
line's sound; the best one speaks if its mean score is > 0 (the spike: 80% right speaking faces on the owner's
episodes)."""
import io
from contextlib import closing

import numpy as np

from app.services.characters import faces, lips, voiceprint
from app.services.characters.types import LinePrints

__all__ = ["LinePrints", "prints_for_spans"]

MIN_TRACK = 10
SAMPLES_PER_FRAME = voiceprint.RATE // faces.FPS


def prints_for_spans(video_path: str, vocals_path: str | None, spans: list[tuple[float, float]],
                     with_faces: bool = True) -> list[LinePrints]:
    """Prints for each (start, end) span, in seconds. Without vocals there is no voice to print and no sound to judge
    a face against, so nothing is read."""
    if vocals_path is None:
        return [LinePrints(None, None, None, None) for _ in spans]
    size = faces.video_size(video_path) if with_faces else None
    result = []
    for start, end in spans:
        start = max(start, 0.0)
        if end <= start:
            result.append(LinePrints(None, None, None, None))
            continue
        audio = voiceprint.read_audio(vocals_path, start, end)
        speaking = _speaking_face(video_path, size, start, end, audio) if size else None
        result.append(LinePrints(voiceprint.voice_print(audio), *(speaking or (None, None, None))))
    return result


def _speaking_face(video_path: str, size: tuple[int, int], start: float, end: float,
                   audio: np.ndarray) -> tuple[np.ndarray, float, bytes] | None:
    """(face print, mean lip-sync score, JPEG) of the face speaking this sound (16 kHz int16 from `start`), or None
    when no face does."""
    tracks: list[faces.Track] = []
    with closing(faces.frames(video_path, start, end, size=size)) as picture:
        for t, frame in picture:
            k = round((t - start) * faces.FPS)
            for face in faces.detect(frame):
                if min(face[2], face[3]) >= faces.MIN_SIDE:
                    faces.follow(tracks, t, face).add(k, t, frame, face)
    best, best_score = None, 0.0
    for track in tracks:
        if len(track.frames) < MIN_TRACK:
            continue
        # from the track's first frame to the end of its last; the crops skip its missed frames, as the spike did
        heard = audio[track.frames[0] * SAMPLES_PER_FRAME:(track.frames[-1] + 1) * SAMPLES_PER_FRAME]
        scores = lips.speaking_scores(heard, np.stack(track.crops))
        if scores is not None and float(np.mean(scores)) > best_score:
            best, best_score = track, float(np.mean(scores))
    if best is None:
        return None
    face = np.mean(best.features, axis=0)
    jpeg = io.BytesIO()
    if best.picture is not None:
        best.picture.save(jpeg, "JPEG", quality=85)
    return (face / np.linalg.norm(face)).astype(np.float32), best_score, jpeg.getvalue() or None
