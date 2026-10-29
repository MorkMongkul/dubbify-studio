"""Where each line starts: at the moment its first word is heard, not just at the nearest pause.

Transcription (app.services.asr) times lines by the pauses in the vocals: when actors talk on without a 0.4 s pause,
several sentences become one stretch, and a line can start a second or more before its words (measured 2026-10-29 on
the owner's 26 episodes since 0.3.0: for the lines whose words a speech model finds reliably, Movelo's start was more
than 0.5 s before the first word on 37% and after it on 14%; finer pause settings didn't help). So after
transcription a local Mandarin speech model (SenseVoice, int8, run by sherpa-onnx) marks when every character is
heard, the episode's text is matched to those characters in order, and a line whose words are found reliably
(MIN_MATCHED characters and MIN_SHARE of the line) starts LEAD_SECS before its first one — when that is more than
MOVE_OVER_SECS from where the pauses put it. Against the owner's own corrections: lines more than 1 s off 39% → 27%,
median 0.61 → 0.41 s, and 204 of 226 lines already right kept.

The model (Apache-2.0, converted by k2-fsa from ASLP-lab/WSYue-ASR's sensevoice_small_yue) isn't in the app: the
packaged app downloads it once into its data folder (ensure_model, in the background at start-up; ARCHIVE_SHA256
checked), so updates stay small. Without it, below macOS 13.4, for a language other than Chinese, or when anything
fails, lines keep the pauses' timing — refine never fails an analysis."""
import asyncio
import bz2  # noqa: F401 — tarfile imports it only when it opens a .bz2: named here so PyInstaller bundles it
import hashlib
import logging
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
from difflib import SequenceMatcher
from pathlib import Path

import httpx
import numpy as np

from app.core.binaries import ffmpeg
from app.core.config import settings

logger = logging.getLogger(__name__)

ARCHIVE_DIR = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09"
ARCHIVE_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{ARCHIVE_DIR}.tar.bz2"
ARCHIVE_SHA256 = "7305f7905bfcf77fa0b39388a313f3da35c68d971661a65475b56fb2162c8e63"   # 165,783,878 bytes
FILES = ("model.int8.onnx", "tokens.txt")

LEAD_SECS = 0.1         # a line starts this long before its first character is heard (the owner's own lead: 0.07 s)
MOVE_OVER_SECS = 1.0    # ...when the pauses put it further than this from it (0.8–1.0 measured alike; 1.0 kept most)
MIN_MATCHED = 2         # characters of the line found in what was heard...
MIN_SHARE = 0.5         # ...and at least this share of them
END_PAD_SECS = 0.3      # a line moved past its own end ends this long after its last character
MIN_LINE_SECS = 0.3
RATE = 16000

_KEEP = re.compile(r"[㐀-鿿0-9a-zA-Z]")
_download_lock = asyncio.Lock()
_model_lock = threading.Lock()   # one model at a time (~0.3 GB while it runs)


def model_dir() -> Path | None:
    """The model's folder in the app's data folder; None without one (dev, tests): nothing is downloaded there."""
    if not settings.DUBIFY_DATA_DIR:
        return None
    return Path(settings.DUBIFY_DATA_DIR) / "models" / ARCHIVE_DIR


def model_ready() -> bool:
    folder = model_dir()
    return folder is not None and all((folder / name).is_file() for name in FILES)


async def ensure_model() -> bool:
    """Downloads and unpacks the model when it isn't there yet. True when it's ready. Never raises: a failed download
    leaves nothing behind and is tried again at the next start-up."""
    from app.services.characters import models
    folder = model_dir()
    if folder is None or not models.runtime_supported():   # below macOS 13.4 sherpa-onnx can't run it
        return False
    async with _download_lock:
        if model_ready():
            return True
        folder.parent.mkdir(parents=True, exist_ok=True)
        part = folder.parent / f"{ARCHIVE_DIR}.tar.bz2.part"
        try:
            digest = hashlib.sha256()
            async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30.0, read=120.0)) as client:
                async with client.stream("GET", ARCHIVE_URL) as response:
                    response.raise_for_status()
                    with open(part, "wb") as out:
                        async for chunk in response.aiter_bytes(1 << 20):
                            digest.update(chunk)
                            out.write(chunk)
            if digest.hexdigest() != ARCHIVE_SHA256:
                raise ValueError(f"the download's SHA-256 is {digest.hexdigest()}, expected {ARCHIVE_SHA256}")
            await asyncio.to_thread(_unpack, part, folder)
            logger.info(f"Line timing: speech model ready in {folder}")
            return True
        except Exception as e:   # noqa: BLE001 — offline, GitHub down, a full disk: lines keep the pauses' timing
            logger.warning(f"Line timing: couldn't download the speech model, lines keep the pauses' timing: {e}")
            return False
        finally:
            part.unlink(missing_ok=True)


def _unpack(archive: Path, folder: Path) -> None:
    """Only the two files the model needs, written by name (never a path from the archive), swapped in whole."""
    staging = Path(tempfile.mkdtemp(prefix="unpacking-", dir=folder.parent))
    try:
        wanted = {f"{ARCHIVE_DIR}/{name}": name for name in FILES}
        with tarfile.open(archive, "r:bz2") as tar:
            for member in tar:
                if member.isfile() and member.name in wanted:
                    with tar.extractfile(member) as src, open(staging / wanted[member.name], "wb") as dst:
                        shutil.copyfileobj(src, dst)
        missing = [name for name in FILES if not (staging / name).is_file()]
        if missing:
            raise ValueError(f"the archive has no {', '.join(missing)}")
        if folder.exists():
            shutil.rmtree(folder)
        os.replace(staging, folder)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _chars(text: str) -> list[str]:
    from app.services.asr import _to_simplified
    return _KEEP.findall(_to_simplified(text or ""))


def refined_spans(lines: list[tuple[float, float, str]], heard: list[tuple[str, float]]) -> list[tuple[float, float]]:
    """Each line's (start, end): the episode's text matched, in order, to the characters heard (with their times)."""
    text, owner = [], []
    for i, (_, _, words) in enumerate(lines):
        chars = _chars(words)
        text += chars
        owner += [i] * len(chars)
    hits: dict[int, list[float]] = {}
    matcher = SequenceMatcher(None, text, [c for c, _ in heard], autojunk=False)
    for a, b, size in matcher.get_matching_blocks():
        for k in range(size):
            hits.setdefault(owner[a + k], []).append(heard[b + k][1])
    spans = []
    for i, (start, end, words) in enumerate(lines):
        found = hits.get(i, [])
        if len(found) >= MIN_MATCHED and len(found) >= MIN_SHARE * len(_chars(words)):
            first = max(0.0, round(min(found) - LEAD_SECS, 3))
            if abs(first - start) > MOVE_OVER_SECS:
                start = first
                if start > end - MIN_LINE_SECS:
                    end = round(max(end, max(found) + END_PAD_SECS), 3)
        spans.append((start, end))
    return spans


def _windows(vocals: str, duration: float) -> list[tuple[float, float]]:
    """The stretches to decode: speech found more generously than transcription does, a little wider, ≤ 20 s."""
    from app.services import asr
    done = subprocess.run([ffmpeg(), "-i", vocals, "-af", "silencedetect=noise=-40dB:d=0.3", "-f", "null", "-"],
                          capture_output=True)
    raw = asr._invert_to_speech_spans(asr._parse_silencedetect_log(done.stderr.decode(errors="ignore")), duration)
    merged: list[tuple[float, float]] = []
    for s, e in raw:
        if merged and s - merged[-1][1] <= 0.3 and e - merged[-1][0] <= 20.0:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return [(max(0.0, s - 0.3), min(duration, e + 0.3)) for s, e in merged]


def heard_characters(vocals: str) -> list[tuple[str, float]]:
    """Blocking (ffmpeg, the model): every character the model hears in the vocals, with when (seconds)."""
    from app.services.characters import models
    if not models.runtime_supported():
        raise ImportError("the speech model needs macOS 13.4 or later")
    import sherpa_onnx

    done = subprocess.run([ffmpeg(), "-v", "error", "-i", vocals, "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
                          capture_output=True, check=True)
    audio = np.frombuffer(done.stdout, np.int16).astype(np.float32) / 32768
    folder = model_dir()
    out: list[tuple[str, float]] = []
    with _model_lock:
        recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(folder / "model.int8.onnx"), tokens=str(folder / "tokens.txt"), num_threads=models.THREADS,
            use_itn=False, language="zh")
        for lo, hi in _windows(vocals, len(audio) / RATE):
            stream = recognizer.create_stream()
            stream.accept_waveform(RATE, audio[int(lo * RATE):int(hi * RATE)])
            recognizer.decode_stream(stream)
            result = stream.result
            for token, t in zip(result.tokens, result.timestamps):
                out += [(c, lo + t) for c in _chars(token)]
    return out


async def refine(vocals: str, lines, language: str) -> int:
    """Moves freshly transcribed lines (start_time/end_time, source_text) to where their words are heard. Returns how
    many moved. Never raises: whatever goes wrong, the lines keep the pauses' timing."""
    if not (language or "").lower().startswith("zh") or not model_ready():
        return 0
    try:
        heard = await asyncio.to_thread(heard_characters, vocals)
        spans = refined_spans([(line.start_time, line.end_time, line.source_text) for line in lines], heard)
    except Exception as e:   # noqa: BLE001
        logger.warning(f"Line timing: kept the pauses' timing: {e}")
        return 0
    moved = 0
    for line, (start, end) in zip(lines, spans):
        if (start, end) != (line.start_time, line.end_time):
            line.start_time, line.end_time = start, end
            moved += 1
    logger.info(f"Line timing: {moved} of {len(lines)} line(s) moved to where their first word is heard")
    return moved
