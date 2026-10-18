"""
app/services/auto_voice.py
A voice for every speaker.

A speaker nobody gave a voice used to be voiced from a blank description with
no fixed voice, so each of its lines came out as a different random voice.
Now, the first time such a speaker is voiced, it gets one of the built-in
voices and keeps it:

- a man's, a woman's or a child's voice, by the median pitch of the actor's
  original lines (the episode's separated vocals);
- one no other speaker in the project uses, when there is one left;
- saved on the speaker with voice_auto=True: the user sees it in Speakers and
  can change it, and a picked voice doesn't count as the user's choice
  (pipeline_common.is_confirmed_speaker).

A line with no speaker gets a fixed voice by its own pitch (nothing to save it
on). Without built-in voices installed, the old description-based voice stays.
"""
import asyncio
import logging
import weakref
from typing import Optional

import numpy as np
import soundfile as sf
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Job, Segment, Speaker, Voice
from app.services.builtin_voices import PREFIX
from app.services.pipeline_common import vocals_path_for
from app.services.voice_tags import voice_section

logger = logging.getLogger(__name__)

MAN_BELOW_HZ = 165.0      # median pitch below this: a man's voice
CHILD_ABOVE_HZ = 265.0    # above this: a child's voice; in between: a woman's
MAX_LINES = 12            # a speaker's longest lines are enough to measure

# Voices to pick from for each kind, in order of preference (narrators and
# elders are left for when they're chosen on purpose)
SECTIONS = {
    "man": ("young_men", "older_men"),
    "woman": ("young_women", "older_women"),
    "child": ("teens_children",),
    None: ("young_men", "young_women", "older_men", "older_women"),
}

# Batch voicing runs several lines at once: one speaker must get one voice,
# and two speakers mustn't both take the same free one. One lock per event
# loop (a lock is bound to the loop that first waits on it).
_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = weakref.WeakKeyDictionary()


def _assigning() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    if loop not in _locks:
        _locks[loop] = asyncio.Lock()
    return _locks[loop]


def median_pitch(samples: np.ndarray, rate: int, fmin: float = 70.0, fmax: float = 450.0) -> Optional[float]:
    """Median fundamental frequency (Hz) over the voiced 40 ms frames, or None for silence."""
    n, hop = int(0.04 * rate), int(0.01 * rate)
    if len(samples) < n:
        return None
    lo, hi = int(rate // fmax), int(rate // fmin)
    frames = [samples[i:i + n] for i in range(0, len(samples) - n + 1, hop)]
    levels = np.array([np.sqrt(np.mean(f * f)) for f in frames])
    if levels.max() <= 1e-4:
        return None
    pitches = []
    for frame, level in zip(frames, levels):
        if level < 0.1 * levels.max():
            continue
        frame = frame - frame.mean()
        spec = np.fft.rfft(frame, 2 * n)
        ac = np.fft.irfft(spec * np.conj(spec))[:n]          # autocorrelation
        pitches.append(rate / (lo + int(np.argmax(ac[lo:hi]))))
    return float(np.median(pitches)) if pitches else None


def kind_from_pitch(hz: Optional[float]) -> Optional[str]:
    if hz is None:
        return None
    return "man" if hz < MAN_BELOW_HZ else "child" if hz > CHILD_ABOVE_HZ else "woman"


def _pitch_of_spans(spans: list[tuple[str, float, float]]) -> Optional[float]:
    """Median pitch over these (audio file, start, end) stretches; blocking file reads."""
    pitches = []
    for path, start, end in spans:
        try:
            rate = sf.info(path).samplerate
            audio, rate = sf.read(path, start=int(start * rate), stop=int(end * rate),
                                  dtype="float32", always_2d=True)
        except Exception:  # noqa: BLE001 — a missing or unreadable stem just means "unknown"
            continue
        hz = median_pitch(audio.mean(axis=1), rate)
        if hz is not None:
            pitches.append(hz)
    return float(np.median(pitches)) if pitches else None


async def _speaker_kind(db: AsyncSession, speaker: Speaker) -> Optional[str]:
    rows = (await db.execute(
        select(Segment, Job).join(Job, Job.id == Segment.job_id)
        .where(Segment.speaker_id == speaker.id)
        .order_by((Segment.end_time - Segment.start_time).desc())
        .limit(MAX_LINES)
    )).all()
    spans = [(vocals_path_for(job), seg.start_time, seg.end_time) for seg, job in rows]
    hz = await asyncio.to_thread(_pitch_of_spans, [s for s in spans if s[0]])
    return kind_from_pitch(hz)


async def _pick(db: AsyncSession, kind: Optional[str], taken: set[str]) -> Optional[Voice]:
    """The first built-in voice of this kind (library order) that isn't taken, else the first one."""
    wanted = SECTIONS[kind]
    voices = (await db.execute(select(Voice).where(Voice.id.like(f"{PREFIX}%")))).scalars().all()
    ranked = sorted(
        (v for v in voices if voice_section(v.gender, v.age_group, v.roles) in wanted),
        key=lambda v: (wanted.index(voice_section(v.gender, v.age_group, v.roles)), v.id),
    )
    return next((v for v in ranked if v.id not in taken), ranked[0] if ranked else None)


async def ensure_speaker_voice(db: AsyncSession, speaker: Speaker) -> Optional[Voice]:
    """
    The speaker's voice, picking and saving a built-in one if it has none.
    Commits when it picks (so other lines voiced at the same time see it).
    """
    async with _assigning():
        await db.refresh(speaker)            # another line of this speaker may have just picked one
        if speaker.voice_id:
            return await db.get(Voice, speaker.voice_id)
        kind = await _speaker_kind(db, speaker)
        taken = set((await db.execute(
            select(Speaker.voice_id).where(Speaker.project_id == speaker.project_id,
                                           Speaker.id != speaker.id, Speaker.voice_id.is_not(None))
        )).scalars().all())
        voice = await _pick(db, kind, taken)
        if voice:
            speaker.voice_id, speaker.voice_auto = voice.id, True
            await db.commit()
            logger.info(f"Speaker {speaker.display_name or speaker.label} had no voice: "
                        f"picked {voice.name} ({kind or 'unknown'} by pitch)")
        return voice


async def voice_for_line(db: AsyncSession, seg: Segment) -> Optional[Voice]:
    """A fixed built-in voice for a line with no speaker, by the line's own pitch."""
    job = await db.get(Job, seg.job_id)
    path = vocals_path_for(job) if job else None
    hz = await asyncio.to_thread(_pitch_of_spans, [(path, seg.start_time, seg.end_time)]) if path else None
    return await _pick(db, kind_from_pitch(hz), set())
