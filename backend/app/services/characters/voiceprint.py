"""Voice prints: ERes2NetV2 (3D-Speaker, through sherpa-onnx) on the separated vocals, 16 kHz mono."""
import subprocess
from functools import lru_cache

import numpy as np

from app.core.binaries import ffmpeg
from app.services.characters import models

RATE = 16000
MIN_SECONDS = 0.3


def read_audio(path: str, start: float, end: float) -> np.ndarray:
    """The sound from `start` to `end` s, 16 kHz mono int16 (the scale Light-ASD's MFCCs expect).

    Asked for as 16-bit, ffmpeg makes stereo mono as (L + R) / 2, as the spike did; asked for as float it would add
    them at -3 dB each, louder than either channel."""
    result = subprocess.run(
        [ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{start:.6f}", "-to", f"{end:.6f}",
         "-i", path, "-map", "0:a:0", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
        capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg could not read {path}: {result.stderr.decode(errors='replace')[-300:]}")
    return np.frombuffer(result.stdout, np.int16)


@lru_cache(maxsize=1)
def _extractor():
    import sherpa_onnx

    return sherpa_onnx.SpeakerEmbeddingExtractor(
        sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(models.path("eres2netv2")), num_threads=4))


def voice_print(audio: np.ndarray) -> np.ndarray | None:
    """The voice print of 16 kHz mono int16 sound, L2-normalised (192,), or None under 0.3 s."""
    if len(audio) < MIN_SECONDS * RATE:
        return None
    extractor = _extractor()
    stream = extractor.create_stream()
    stream.accept_waveform(RATE, np.asarray(audio, np.float32) / 32768)
    stream.input_finished()
    if not extractor.is_ready(stream):
        return None
    vector = np.array(extractor.compute(stream), np.float32)
    norm = np.linalg.norm(vector)
    return vector / norm if norm > 0 else None
