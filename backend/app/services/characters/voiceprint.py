"""Voice prints on the separated vocals, 16 kHz mono, through sherpa-onnx: a line's print is ERes2NetV2's and CAM++'s
(3D-Speaker's zh/en "advanced") side by side, each normalised and weighed the same, so one cosine averages the two
models' (0.5.3: on the owner's 30 finished episodes 65.7% → 67.8% right; other models — ERes2Net-large, ResNet293,
CN-Celeb ResNet34 — did worse alone and no better together). A voice FILE (the upgrade's Khmer voices and reference
clips) is heard by ERes2NetV2 alone: the upgrade's margin was measured on it."""
import subprocess
from functools import lru_cache

import numpy as np

from app.core.binaries import ffmpeg
from app.services.characters import models

RATE = 16000
MIN_SECONDS = 0.3
SIZE = 384   # a line's print: ERes2NetV2's 192 numbers, then CAM++'s 192


def read_audio(path: str, start: float = 0.0, end: float | None = None) -> np.ndarray:
    """The sound from `start` to `end` s (the whole file without them), 16 kHz mono int16.

    Asked for as 16-bit, ffmpeg makes stereo mono as (L + R) / 2, as the spike did; asked for as float it would add
    them at -3 dB each, louder than either channel."""
    span = ["-ss", f"{start:.6f}"] + (["-to", f"{end:.6f}"] if end is not None else [])
    result = subprocess.run(
        [ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", *span,
         "-i", path, "-map", "0:a:0", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
        capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg could not read {path}: {result.stderr.decode(errors='replace')[-300:]}")
    return np.frombuffer(result.stdout, np.int16)


@lru_cache(maxsize=1)
def _extractor():
    import sherpa_onnx

    return sherpa_onnx.SpeakerEmbeddingExtractor(
        sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(models.path("eres2netv2")), num_threads=models.THREADS))


@lru_cache(maxsize=1)
def _campplus():
    import sherpa_onnx

    return sherpa_onnx.SpeakerEmbeddingExtractor(
        sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(models.path("campplus")), num_threads=models.THREADS))


def _heard(extractor, audio: np.ndarray) -> np.ndarray | None:
    """One model's print of 16 kHz mono int16 sound, L2-normalised, or None under 0.3 s."""
    if len(audio) < MIN_SECONDS * RATE:
        return None
    stream = extractor.create_stream()
    stream.accept_waveform(RATE, np.asarray(audio, np.float32) / 32768)
    stream.input_finished()
    if not extractor.is_ready(stream):
        return None
    vector = np.array(extractor.compute(stream), np.float32)
    norm = np.linalg.norm(vector)
    return vector / norm if norm > 0 else None


def voice_print(audio: np.ndarray) -> np.ndarray | None:
    """A line's voice print (SIZE,): both models' prints side by side, unit length, or None under 0.3 s."""
    first = _heard(_extractor(), audio)
    second = _heard(_campplus(), audio) if first is not None else None
    if first is None or second is None:
        return None
    return (np.concatenate([first, second]) / np.sqrt(2)).astype(np.float32)


def file_print(path: str) -> np.ndarray | None:
    """ERes2NetV2's print (192,) of a whole sound file (a voice's reference clip, a line's Khmer voice), or None under
    0.3 s."""
    return _heard(_extractor(), read_audio(path))
