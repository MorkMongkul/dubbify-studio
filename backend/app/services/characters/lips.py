"""Lip-sync: how well does a face's mouth match the sound? (Light-ASD, MIT, trained on AVA, run as ONNX.)

`speaking_scores` takes the sound of one line and one face followed over it, and returns a score per video frame
(> 0: this face is speaking this sound). The model and its export: scripts/export-light-asd.py."""
from functools import lru_cache

import numpy as np

from app.services.characters import models
from app.services.characters.mfcc import mfcc

VIDEO_FPS = 25
MFCC_PER_SECOND = 100
MIN_FRAMES = 5
DURATIONS = (1, 2, 4)  # seconds; Light-ASD scores a clip at a few lengths and the demo averages them


@lru_cache(maxsize=1)
def _session():
    import onnxruntime

    options = onnxruntime.SessionOptions()
    options.log_severity_level = 3
    return onnxruntime.InferenceSession(str(models.path("lightasd")), options, providers=["CPUExecutionProvider"])


def run_onnx(audio: np.ndarray, video: np.ndarray) -> np.ndarray:
    """The raw model: audio (1, 4T, 13) MFCCs and video (1, T, 112, 112) grey crops (0-255), both float32 -> scores (T,)."""
    return _session().run(None, {"audio": audio.astype(np.float32), "video": video.astype(np.float32)})[0]


def speaking_scores(audio_int16: np.ndarray, crops: np.ndarray) -> np.ndarray | None:
    """Per-frame lip-sync scores for one face over one stretch of sound (16 kHz mono int16), or None for under 5 frames.

    `crops` is (T, 112, 112) uint8 at 25 fps, the first frame at the sound's first sample. No sound (a face followed
    past the end of the vocals) gives None too."""
    if len(audio_int16) == 0:
        return None
    features = mfcc(audio_int16, 16000)
    video = np.asarray(crops)
    length = min((features.shape[0] - features.shape[0] % 4) / MFCC_PER_SECOND, video.shape[0] / VIDEO_FPS)
    features = features[: int(round(length * MFCC_PER_SECOND))]
    video = video[: int(round(length * VIDEO_FPS))]
    if len(video) < MIN_FRAMES:
        return None
    runs = []
    for duration in DURATIONS:
        scores: list[float] = []
        for i in range(int(np.ceil(length / duration))):
            a = features[i * duration * MFCC_PER_SECOND:(i + 1) * duration * MFCC_PER_SECOND]
            v = video[i * duration * VIDEO_FPS:(i + 1) * duration * VIDEO_FPS]
            if a.shape[0] < 4 or v.shape[0] < 1:
                continue
            scores.extend(run_onnx(a[None], v[None]))
        runs.append(np.array(scores[: len(video)]))
    n = min(len(r) for r in runs)
    return np.mean([r[:n] for r in runs], axis=0)
