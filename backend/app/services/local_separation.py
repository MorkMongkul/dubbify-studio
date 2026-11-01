"""Vocals and music separated on this Mac: Spleeter's 2 stems (Deezer, MIT; int8 ONNX converted by k2-fsa), run by
sherpa-onnx, the library character recognition already uses.

Measured 2026-11-01 on the owner's M1 with 3 minutes of a real episode: 2.3 s (3 CPU-s), against ~2 minutes through
the HF Space (SEPARATION_HF_SPACE, which runs the same Spleeter on one CPU thread: an upload, a queue, a download). Its
vocals match the Space's (correlation 0.96 once aligned), and they line up with the original sound: the Space's came
back 46 ms late (its MP3). UVR MDX-Net models were tried too: 28 MB took 35 s, the 63 MB Voc_FT 136 s and heated the
Mac; the owner found Spleeter acceptable.

The model isn't in the app (it must not join models.json: that list is the voice prints' version, and a change makes
every line be heard again): the packaged app downloads its two files once into the data folder (ensure_model, in the
background at start-up; each file's SHA-256 checked). Long sound is separated in pieces of PIECE_SECS that cross-fade
over OVERLAP_SECS (they match the Space as well as one pass does: 0.900 vs 0.893), so memory stays near 1 GB. It runs
in a child process (separate): Spleeter's runtime keeps what it took (1.5 GB after a 4-minute file), and the backend
runs all day; the child gives it all back and can't take the backend down with it. Without the model, below macOS
13.4, or when anything fails, the HF Space separates (source_separator)."""
import asyncio
import hashlib
import logging
import os
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Callable

import httpx
import numpy as np

from app.core.binaries import ffmpeg
from app.core.config import settings

logger = logging.getLogger(__name__)

MODEL_DIR = "sherpa-onnx-spleeter-2stems-int8"
BASE_URL = ("https://huggingface.co/csukuangfj/sherpa-onnx-spleeter-2stems-int8/resolve/"
            "f315b4e699f7eab644977c7e69988733d877f6ee")
FILES = {   # 26,238,864 and 26,238,869 bytes; the same files as sherpa-onnx's source-separation-models release
    "vocals.int8.onnx": "6e9721864c0c1a6d86eaae92de71637b2daeb3df769c9c536053b46be8dd10aa",
    "accompaniment.int8.onnx": "53a96a0d1520b45b8f3cc53262e3725defb686fc3a17490932f050721f4583f1",
}
RATE = 44100
PIECE_SECS = 30.0      # separated at a time: Spleeter peaks at ~0.9 GB for 30 s (60 s: 1.3–1.7 GB; one pass of 4 min: 3.2 GB)
OVERLAP_SECS = 2.0     # neighbouring pieces cross-fade over this

_download_lock = asyncio.Lock()
_model_lock = threading.Lock()   # one separation at a time in this process...
_child_lock = asyncio.Lock()     # ...and one child at a time (each holds ~1 GB)
CHILD_FLAG = "--separate"        # the packaged backend's own executable separates with it (sidecar_entry)


def model_dir() -> Path | None:
    """The model's folder in the app's data folder; None without one (dev, tests): nothing is downloaded there."""
    if not settings.DUBIFY_DATA_DIR:
        return None
    return Path(settings.DUBIFY_DATA_DIR) / "models" / MODEL_DIR


def model_ready() -> bool:
    from app.services.characters import models
    folder = model_dir()
    return (folder is not None and models.runtime_supported()
            and all((folder / name).is_file() for name in FILES))


async def ensure_model() -> bool:
    """Downloads the model's files that aren't there yet. True when it's ready. Never raises: a failed download leaves
    nothing behind and is tried again at the next start-up — except one whose SHA-256 doesn't match, which this app
    version never downloads again (the file upstream changed)."""
    from app.services.characters import models
    folder = model_dir()
    if folder is None or not models.runtime_supported():   # below macOS 13.4 sherpa-onnx can't run it
        return False
    async with _download_lock:
        if model_ready():
            return True
        mismatch = folder.parent / f"{MODEL_DIR}.mismatch"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if mismatch.is_file() and mismatch.read_text().strip() == settings.version:
                return False
            async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30.0, read=120.0)) as client:
                for name, sha256 in FILES.items():
                    if (folder / name).is_file():
                        continue
                    part = folder / f"{name}.part"
                    try:
                        digest = hashlib.sha256()
                        async with client.stream("GET", f"{BASE_URL}/{name}") as response:
                            response.raise_for_status()
                            with open(part, "wb") as out:
                                async for chunk in response.aiter_bytes(1 << 20):
                                    digest.update(chunk)
                                    out.write(chunk)
                        if digest.hexdigest() != sha256:
                            mismatch.write_text(settings.version)
                            raise ValueError(f"{name}'s SHA-256 is {digest.hexdigest()}, expected {sha256}")
                        os.replace(part, folder / name)
                    finally:
                        part.unlink(missing_ok=True)
            logger.info(f"Separation: Spleeter ready in {folder}")
            return True
        except Exception as e:   # noqa: BLE001 — offline, a full disk: the HF Space separates meanwhile
            logger.warning(f"Separation: couldn't download Spleeter, the HF Space separates meanwhile: {e}")
            return False


def separate_samples(samples: np.ndarray, rate: int,
                     run: Callable[[np.ndarray, int], tuple[np.ndarray, np.ndarray]]) -> tuple[np.ndarray, np.ndarray]:
    """(vocals, music) of `samples` (channels × n), run piece by piece: pieces of PIECE_SECS that overlap by
    OVERLAP_SECS and cross-fade linearly there."""
    total = samples.shape[1]
    piece, overlap = int(PIECE_SECS * rate), int(OVERLAP_SECS * rate)
    if total <= piece:
        return run(samples, rate)
    vocals = np.zeros_like(samples)
    music = np.zeros_like(samples)
    weight = np.zeros(total, np.float32)
    start = 0
    while start < total:
        end = min(total, start + piece)
        v, m = run(np.ascontiguousarray(samples[:, start:end]), rate)
        w = np.ones(end - start, np.float32)
        if start > 0:
            w[:overlap] = np.linspace(0.0, 1.0, overlap, endpoint=False, dtype=np.float32)
        if end < total:
            w[-overlap:] = np.minimum(w[-overlap:], np.linspace(1.0, 0.0, overlap, endpoint=False, dtype=np.float32))
        vocals[:, start:end] += v[:, :end - start] * w
        music[:, start:end] += m[:, :end - start] * w
        weight[start:end] += w
        if end == total:
            break
        start = end - overlap
    weight[weight == 0] = 1.0
    return vocals / weight, music / weight


def _fit(stem, shape: tuple[int, int]) -> np.ndarray:
    """A stem as long as its input: Spleeter works in whole frames, so it can come back ~1,000 samples short."""
    data = np.asarray(stem, np.float32).reshape(shape[0], -1)[:, :shape[1]]
    return np.pad(data, ((0, 0), (0, shape[1] - data.shape[1])))


def _read(path: str) -> np.ndarray:
    """The sound at RATE, stereo, float32 (2 × n)."""
    result = subprocess.run(
        [ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-i", path,
         "-map", "0:a:0", "-ac", "2", "-ar", str(RATE), "-f", "f32le", "-"], capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg could not read {path}: {result.stderr.decode(errors='replace')[-300:]}")
    return np.frombuffer(result.stdout, np.float32).reshape(-1, 2).T


def _write(path: str, samples: np.ndarray) -> None:
    """16-bit stereo WAV at RATE, as the Space's stems were transcoded."""
    block = RATE * 10   # converted 10 s at a time: no second full-length copy
    with wave.open(path, "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(RATE)
        for start in range(0, samples.shape[1], block):
            part = samples[:, start:start + block].T
            out.writeframes((np.clip(part, -1.0, 1.0) * 32767).astype("<i2").tobytes())


def separate_file(audio_path: str, vocals_target: str, bgm_target: str) -> None:
    """Blocking: the audio's vocals and music written as WAVs. Raises when it can't (the caller asks the Space)."""
    import sherpa_onnx
    from app.services.characters import models

    folder = model_dir()
    config = sherpa_onnx.OfflineSourceSeparationConfig(model=sherpa_onnx.OfflineSourceSeparationModelConfig(
        spleeter=sherpa_onnx.OfflineSourceSeparationSpleeterModelConfig(
            vocals=str(folder / "vocals.int8.onnx"), accompaniment=str(folder / "accompaniment.int8.onnx")),
        num_threads=models.THREADS, provider="cpu"))

    with _model_lock:
        began = time.monotonic()
        separator = sherpa_onnx.OfflineSourceSeparation(config)

        def run(piece: np.ndarray, rate: int) -> tuple[np.ndarray, np.ndarray]:
            out = separator.process(sample_rate=rate, samples=np.ascontiguousarray(piece))
            if out.sample_rate != rate or len(out.stems) != 2:
                raise RuntimeError(f"Spleeter gave {len(out.stems)} stems at {out.sample_rate} Hz")
            return _fit(out.stems[0].data, piece.shape), _fit(out.stems[1].data, piece.shape)

        samples = _read(audio_path)
        if samples.shape[1] == 0:
            raise RuntimeError("the audio is empty")
        vocals, music = separate_samples(samples, RATE, run)
        _write(vocals_target, vocals)
        _write(bgm_target, music)
    logger.info(f"Separated on this Mac in {time.monotonic() - began:.1f} s ({samples.shape[1] / RATE:.0f} s of sound)")


async def separate(audio_path: str, vocals_target: str, bgm_target: str, timeout: float) -> None:
    """separate_file in a child process (the packaged backend's own executable with CHILD_FLAG; `python -m` in
    development), one at a time. Raises when it fails or takes longer than `timeout` (the child is stopped)."""
    if getattr(sys, "frozen", False):
        command = [sys.executable, CHILD_FLAG, audio_path, vocals_target, bgm_target]
    else:
        command = [sys.executable, "-m", __name__, audio_path, vocals_target, bgm_target]
    async with _child_lock:
        began = time.monotonic()
        child = await asyncio.create_subprocess_exec(
            *command, cwd=str(Path(__file__).resolve().parents[2]),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            _, stderr = await asyncio.wait_for(child.communicate(), timeout)
        except BaseException:   # a timeout, or the job cancelled: the child mustn't outlive it
            child.kill()
            await child.wait()
            raise
        if child.returncode != 0:
            raise RuntimeError(f"exit {child.returncode}: {stderr.decode(errors='replace')[-400:]}")
    logger.info(f"Separated on this Mac in {time.monotonic() - began:.1f} s")


def main(argv: list[str]) -> int:
    """The child: separate_file(audio, vocals, music)."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    separate_file(*argv)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
