"""Where each line starts: at the moment its first word is heard, not just at the nearest pause.

Transcription (app.services.asr) times lines by the pauses in the vocals: when actors talk on without a 0.4 s pause,
several sentences become one stretch, and a line can start a second or more before its words (measured 2026-10-29 on
the owner's 26 episodes since 0.3.0: for the lines whose words a speech model finds reliably, Movelo's start was more
than 0.5 s before the first word on 37% and after it on 14%; finer pause settings didn't help). So after
transcription a local Mandarin speech model (SenseVoice, int8, run by sherpa-onnx) marks when every character is
heard, the episode's text is matched to those characters in order, and a line whose words are found reliably
(MIN_MATCHED characters, MIN_SHARE of the line, not scattered) starts LEAD_SECS before its first one — when that is
more than MOVE_OVER_SECS from where the pauses put it, and only in order: never past a neighbour, never overlapping one
(the review of 0.5.2: unconstrained moves gained more, 39% → 27% of lines more than 1 s off, but put 63 lines over
others — never sure in recognition — and could carry a missed 好的 onto a later one). Against the owner's own
corrections: lines more than 1 s off 39% → 33%, median 0.61 → 0.45 s, 115 of 553 moved, 211 of 226 already right
kept, none out of order or overlapping. Long stretches are decoded in pieces of at most MAX_WINDOW_SECS.

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
MAX_SECS_PER_CHAR = 0.4  # the found characters span at most this per character of the line (+ 0.3 s): else scattered
END_PAD_SECS = 0.3      # a line moved past its own end ends this long after its last character
MIN_LINE_SECS = 0.3
MAX_WINDOW_SECS = 20.0  # SenseVoice attends to a whole window at once: memory grows with its square
RATE = 16000
LOUDNESS_RATE = 100     # loudness frames per second, to cut a long stretch where it's quietest

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
    leaves nothing behind and is tried again at the next start-up — except one whose SHA-256 doesn't match, which this
    app version never downloads again (the file upstream changed: 158 MB thrown away at every launch otherwise)."""
    from app.services.characters import models
    folder = model_dir()
    if folder is None or not models.runtime_supported():   # below macOS 13.4 sherpa-onnx can't run it
        return False
    async with _download_lock:
        if model_ready():
            return True
        part = folder.parent / f"{ARCHIVE_DIR}.tar.bz2.part"
        mismatch = folder.parent / f"{ARCHIVE_DIR}.mismatch"
        try:
            folder.parent.mkdir(parents=True, exist_ok=True)
            for stale in folder.parent.glob("unpacking-*"):   # left by an app that quit while unpacking
                shutil.rmtree(stale, ignore_errors=True)
            if mismatch.is_file() and mismatch.read_text().strip() == settings.version:
                return False
            digest = hashlib.sha256()
            async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30.0, read=120.0)) as client:
                async with client.stream("GET", ARCHIVE_URL) as response:
                    response.raise_for_status()
                    with open(part, "wb") as out:
                        async for chunk in response.aiter_bytes(1 << 20):
                            digest.update(chunk)
                            out.write(chunk)
            if digest.hexdigest() != ARCHIVE_SHA256:
                mismatch.write_text(settings.version)
                raise ValueError(f"the download's SHA-256 is {digest.hexdigest()}, expected {ARCHIVE_SHA256}")
            await asyncio.to_thread(_unpack, part, folder)
            logger.info(f"Line timing: speech model ready in {folder}")
            return True
        except Exception as e:   # noqa: BLE001 — offline, GitHub down, a full disk: lines keep the pauses' timing
            logger.warning(f"Line timing: couldn't download the speech model, lines keep the pauses' timing: {e}")
            return False
        finally:
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass


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
    originals = [(start, end) for start, end, _ in lines]
    count = len(lines)
    # (first heard − LEAD_SECS, last heard) for each line found reliably and far from where the pauses put it
    candidate: list[tuple[float, float] | None] = []
    for i, (start, _, words) in enumerate(lines):
        found = hits.get(i, [])
        n = len(_chars(words))
        if (len(found) < MIN_MATCHED or len(found) < MIN_SHARE * n
                or max(found) - min(found) > MAX_SECS_PER_CHAR * n + 0.3):   # too little heard, or scattered
            candidate.append(None)
            continue
        first = max(0.0, round(min(found) - LEAD_SECS, 3))
        candidate.append((first, max(found)) if abs(first - start) > MOVE_OVER_SECS else None)
    starts, ends = [s for s, _ in originals], [e for _, e in originals]
    moved = [False] * count
    # In order: after the previous line's (new) start and before where the next one is going — lines of one stretch
    # often move together
    for i, found in enumerate(candidate):
        if found is None:
            continue
        after = starts[i - 1] + MIN_LINE_SECS if i else 0.0
        before = float("inf")
        if i + 1 < count:
            before = (candidate[i + 1][0] if candidate[i + 1] else originals[i + 1][0]) - MIN_LINE_SECS
        if after <= found[0] <= before:
            starts[i], moved[i] = found[0], True
    # ...and never past a neighbour in the end (a missed 好的 can't land on a later 好的: the review's case): a move
    # that left two lines out of order goes back, until none does
    changed = True
    while changed:
        changed = False
        for i in range(count - 1):
            if starts[i + 1] - starts[i] < MIN_LINE_SECS and (moved[i] or moved[i + 1]):
                k = i if moved[i] else i + 1
                starts[k], moved[k], changed = originals[k][0], False, True
    for i in range(count):
        if moved[i] and starts[i] > ends[i] - MIN_LINE_SECS:      # moved past its own end
            ends[i] = round(max(ends[i], candidate[i][1] + END_PAD_SECS), 3)
    # No overlaps made: the line before a moved one ends where it now starts (when it ran into it, or the two shared a
    # stretch split by text length), and a moved line ends where the next one starts
    for i in range(1, count):
        if moved[i] and (ends[i - 1] > starts[i] or abs(originals[i - 1][1] - originals[i][0]) < 0.02):
            ends[i - 1] = max(starts[i - 1] + MIN_LINE_SECS, starts[i])
    for i in range(count - 1):
        if moved[i] and ends[i] > starts[i + 1]:
            ends[i] = max(starts[i] + MIN_LINE_SECS, starts[i + 1])
    return list(zip(starts, ends))


def windows(speech: list[tuple[float, float]], loudness: np.ndarray, rate: int,
            duration: float) -> list[tuple[float, float]]:
    """The stretches to decode, from the speech spans: neighbours a short gap apart joined, a little wider, never
    overlapping (nothing decoded twice), and none longer than MAX_WINDOW_SECS — a longer one (continuous speech, or a
    mix with music when separation fell back) is cut where it's quietest in its last 5 s. `loudness`: `rate` values
    per second of the vocals."""
    merged: list[tuple[float, float]] = []
    for s, e in speech:
        if merged and s - merged[-1][1] <= 0.3 and e - merged[-1][0] <= MAX_WINDOW_SECS:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    out: list[tuple[float, float]] = []
    for s, e in merged:
        lo, hi = max(0.0, s - 0.3), min(duration, e + 0.3)
        if out and lo < out[-1][1]:
            lo = out[-1][1]
        while hi - lo > MAX_WINDOW_SECS:
            a, b = int((lo + MAX_WINDOW_SECS - 5.0) * rate), int((lo + MAX_WINDOW_SECS) * rate)
            quiet = loudness[a:b]
            cut = (a + int(np.argmin(quiet))) / rate if len(quiet) else lo + MAX_WINDOW_SECS
            out.append((lo, cut))
            lo = cut
        if hi > lo:
            out.append((lo, hi))
    return out


def _speech(vocals: str, duration: float) -> list[tuple[float, float]]:
    """Speech found more generously than transcription does (-40 dB, pauses of 0.3 s)."""
    from app.services import asr
    done = subprocess.run([ffmpeg(), "-i", vocals, "-af", "silencedetect=noise=-40dB:d=0.3", "-f", "null", "-"],
                          capture_output=True)
    return asr._invert_to_speech_spans(asr._parse_silencedetect_log(done.stderr.decode(errors="ignore")), duration)


def heard_characters(vocals: str) -> list[tuple[str, float]]:
    """Blocking (ffmpeg, the model): every character the model hears in the vocals, with when (seconds)."""
    from app.services.characters import models
    if not models.runtime_supported():
        raise ImportError("the speech model needs macOS 13.4 or later")
    import sherpa_onnx

    done = subprocess.run([ffmpeg(), "-v", "error", "-i", vocals, "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
                          capture_output=True, check=True)
    audio = np.frombuffer(done.stdout, np.int16).astype(np.float32) / 32768
    step = RATE // LOUDNESS_RATE
    frames = audio[:len(audio) // step * step].reshape(-1, step)
    loudness = np.sqrt((frames ** 2).mean(axis=1)) if len(frames) else np.zeros(0, np.float32)
    duration = len(audio) / RATE
    folder = model_dir()
    out: list[tuple[str, float]] = []
    with _model_lock:
        recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(folder / "model.int8.onnx"), tokens=str(folder / "tokens.txt"), num_threads=models.THREADS,
            use_itn=False, language="zh")
        for lo, hi in windows(_speech(vocals, duration), loudness, LOUDNESS_RATE, duration):
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
    given = [(line.start_time, line.end_time, line.source_text) for line in lines]

    def work():   # the model and the matching (0.7 s for 45 min of lines): off the event loop
        return refined_spans(given, heard_characters(vocals))
    try:
        spans = await asyncio.to_thread(work)
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
