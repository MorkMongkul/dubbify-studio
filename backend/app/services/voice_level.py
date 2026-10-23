"""
app/services/voice_level.py
A voice clip's length and loudness, measured on the audio itself.

Measured on a real project (6 episodes, the team's own voices and timings):
- The voice engine starts each voice with ~0.6 s of silence (up to 1 s) before
  the first word, so the voice came in late and ran past its line. trim_silence
  keeps KEEP seconds around the words: 48 of 69 voices then fit their line
  instead of 10.
- Every voice came out at about the same level (-22..-19 dBFS) while the
  original lines ranged over 12 dB (-32..-20): the dub sat on top of the music
  in one line and under the effects in the next. match_gain_db is the gain that
  gives a voice the loudness of the original line it replaces, measured in the
  separated vocals, so the dub sits in the music and effects as the original
  dialogue did.

Both are decided once, when a voice is made (level_new_voice: the new
recording is trimmed before it becomes the raw voice, and the gain is stored on
the line as level_gain_db); render_clip — every later render (volume/filter/
speed change, Auto-fit) — re-uses them. A voice made before 0.2.8 has no stored
gain and renders exactly as it did: re-levelling and trimming it on its first
touch put it ~10 dB and 0.6 s off its untouched neighbours.
"""
import asyncio
import logging
from pathlib import Path

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)

FRAME = 0.01              # seconds per level frame when trimming
LEVEL_FRAME = 0.05        # seconds per frame when measuring loudness
SILENCE_DB = -50.0        # quieter frames are silence (the engine's own is ~-100 dBFS)
KEEP = 0.05               # silence kept before and after the words
NO_SPEECH_DB = -55.0      # an original line quieter than this holds no speech to match
GAIN_MIN, GAIN_MAX = -15.0, 10.0
CEILING_DB = -1.0         # a boosted voice never peaks above this


def _mono(x: np.ndarray) -> np.ndarray:
    return x.mean(axis=1) if x.ndim == 2 else x


def _frame_db(x: np.ndarray, rate: int, seconds: float) -> np.ndarray:
    n = max(1, int(seconds * rate))
    m = len(x) // n
    if m == 0:
        return np.array([])
    rms = np.sqrt(np.mean(x[:m * n].reshape(m, n) ** 2, axis=1))
    return 20 * np.log10(rms + 1e-12)


def voiced_level_db(samples: np.ndarray, rate: int) -> float:
    """Loudness of the speech in `samples`: RMS of the louder half of its 50 ms frames, so pauses don't count."""
    x = _mono(samples)
    n = max(1, int(LEVEL_FRAME * rate))
    m = len(x) // n
    if m == 0:
        return -120.0
    rms = np.sqrt(np.mean(x[:m * n].reshape(m, n) ** 2, axis=1))
    loud = np.sort(rms)[m // 2:]
    return float(20 * np.log10(np.sqrt(np.mean(loud ** 2)) + 1e-12))


def trim_silence(path: str) -> float:
    """Cut the silence before and after the words, keeping KEEP seconds; rewrites the file. Returns its duration."""
    info = sf.info(path)
    data, rate = sf.read(path, dtype="float32", always_2d=True)
    levels = _frame_db(_mono(data), rate, FRAME)
    voiced = np.where(levels > SILENCE_DB)[0]
    if len(voiced) == 0:
        return len(data) / rate                   # no words: leave it as it is
    n = int(FRAME * rate)
    start = max(0, voiced[0] * n - int(KEEP * rate))
    end = min(len(data), (voiced[-1] + 1) * n + int(KEEP * rate))
    if start > 0 or end < len(data):
        sf.write(path, data[start:end], rate, subtype=info.subtype)
    return (end - start) / rate


def match_gain_db(voice_path: str, vocals_path: str, start: float, end: float) -> float:
    """
    The gain that gives the voice the loudness of the original line (`start`–`end`
    in the separated vocals), within GAIN_MIN..GAIN_MAX and never peaking above
    CEILING_DB. 0 when the original holds no speech there.
    """
    voice, rate = sf.read(voice_path, dtype="float32", always_2d=True)
    vocals_rate = sf.info(vocals_path).samplerate
    original, _ = sf.read(vocals_path, start=int(start * vocals_rate), stop=int(end * vocals_rate),
                          dtype="float32", always_2d=True)
    target = voiced_level_db(original, vocals_rate)
    if target < NO_SPEECH_DB or len(voice) == 0:
        return 0.0
    gain = min(GAIN_MAX, max(GAIN_MIN, target - voiced_level_db(voice, rate)))
    peak = float(20 * np.log10(np.abs(voice).max() + 1e-12))
    return round(min(gain, CEILING_DB - peak), 1)


def peak_db(path: str) -> float:
    data, _ = sf.read(path, dtype="float32")
    return float(20 * np.log10(np.abs(data).max() + 1e-12)) if len(data) else -120.0


def original_vocals(job_dir: Path) -> str | None:
    """The episode's separated vocals — None when separation failed and they're the whole mix, music included."""
    from app.services.source_separator import UNAVAILABLE_MARKER
    vocals = job_dir / "vocals.wav"
    return str(vocals) if vocals.exists() and not (job_dir / UNAVAILABLE_MARKER).exists() else None


async def render_clip(seg, raw_path: Path, final_path: Path) -> float:
    """
    Make the segment's final clip from its raw voice: the stored level gain plus
    the user's own volume (never peaking above CEILING_DB), then their filter
    and speed. A voice without a stored gain (made before 0.2.8) gets the
    user's volume alone, as it always did. Returns the clip's duration.
    """
    from app.services.audio_extractor import apply_audio_effects
    volume = seg.volume_db or 0.0
    if seg.level_gain_db is not None:
        volume += seg.level_gain_db
        try:
            volume = min(volume, CEILING_DB - await asyncio.to_thread(peak_db, str(raw_path)))
        except Exception as e:  # noqa: BLE001 — an unreadable clip fails in ffmpeg below, as before
            logger.warning(f"Couldn't read the voice's peak: {e}")
    return await apply_audio_effects(
        input_path=str(raw_path),
        output_path=str(final_path),
        volume_db=round(volume, 1),
        voice_filter=seg.voice_filter,
        voice_speed=seg.voice_speed,
    )


async def level_new_voice(seg, raw_path: Path, final_path: Path) -> float:
    """
    A just-made voice: decide its level once — the loudness of the original line
    where the line is now (0 when separation failed or nothing is said there) —
    store it on the line, and render the clip. Returns the clip's duration.
    """
    gain = 0.0
    vocals = original_vocals(Path(final_path).parent.parent)
    if vocals:
        try:
            gain = await asyncio.to_thread(match_gain_db, str(raw_path), vocals, seg.start_time, seg.end_time)
        except Exception as e:  # noqa: BLE001 — an unreadable stem just means "leave the level alone"
            logger.warning(f"Couldn't measure the original line's loudness: {e}")
    seg.level_gain_db = gain
    return await render_clip(seg, raw_path, final_path)
