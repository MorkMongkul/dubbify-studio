"""
app/services/auto_voice.py
A voice for every speaker.

A speaker nobody gave a voice used to be voiced from a blank description with
no fixed voice, so each of its lines came out as a different random voice.
Now, the first time such a speaker is voiced, it gets one of the built-in
voices and keeps it:

- a man's, a woman's or a child's voice, by the median pitch of the actor's
  original lines in the episode's separated vocals (when separation failed the
  "vocals" are the full mix, whose music skews pitch: the kind is unknown, and
  unknown speakers alternate between men's and women's voices);
- one no other speaker of the same episode uses, nor any voice the user chose
  by hand in the project (each episode labels unnamed characters afresh, so
  counting the whole series ran the pool dry); then the least-used one;
- spread across characters (every character's first take before any second
  take); never a voice the manifest retired;
- saved on the speaker with voice_auto=True: the user sees it in Speakers and
  can change it, and a picked voice alone doesn't count as the user's choice
  (pipeline_common.is_confirmed_speaker).

A line with no speaker gets a fixed voice by its own pitch (nothing to save it
on). Without built-in voices installed, the old description-based voice stays.
"""
import asyncio
import functools
import logging
import weakref
from collections import Counter
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.models import AgeGroup, Gender, Job, Segment, Speaker, Voice
from app.services.builtin_voices import PREFIX, load_manifest
from app.services.source_separator import UNAVAILABLE_MARKER
from app.services.voice_tags import voice_section

logger = logging.getLogger(__name__)

MAN_BELOW_HZ = 165.0      # median pitch below this: a man's voice
CHILD_ABOVE_HZ = 265.0    # above this: a child's voice; in between: a woman's
MAX_LINES = 12            # a speaker's longest lines are enough to measure
VOICED = 0.5              # a frame is voice when its period repeats this strongly (noise doesn't)

# Voices to pick from for each kind, in order of preference (narrators and
# elders are left for when they're chosen on purpose)
SECTIONS = {
    "man": ("young_men", "older_men"),
    "woman": ("young_women", "older_women"),
    "child": ("teens_children",),
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
    """Median fundamental frequency (Hz) over the voiced 40 ms frames, or None for silence or noise."""
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
        lag = lo + int(np.argmax(ac[lo:hi]))
        if ac[0] <= 0 or ac[lag] < VOICED * ac[0]:
            continue                                         # no clear period: noise, breath, a consonant
        pitches.append(rate / lag)
    return float(np.median(pitches)) if pitches else None


def kind_from_pitch(hz: Optional[float]) -> Optional[str]:
    if hz is None:
        return None
    return "man" if hz < MAN_BELOW_HZ else "child" if hz > CHILD_ABOVE_HZ else "woman"


def _separated_vocals(job: Optional[Job]) -> Optional[str]:
    """The episode's vocals stem — unless separation failed and it's a copy of the full mix."""
    if job is None:
        return None
    folder = Path(settings.UPLOAD_DIR) / job.project_id / job.id
    stem = folder / "vocals.wav"
    return str(stem) if stem.exists() and not (folder / UNAVAILABLE_MARKER).exists() else None


def _pitch_of_spans(spans: list[tuple[str, float, float]]) -> Optional[float]:
    """Median pitch over these (audio file, start, end) stretches; blocking file reads."""
    pitches = []
    for path, start, end in spans:
        try:
            rate = sf.info(path).samplerate
            audio, rate = sf.read(path, start=int(start * rate), stop=int(end * rate),
                                  dtype="float32", always_2d=True)
            hz = median_pitch(audio.mean(axis=1), rate)
        except Exception:  # noqa: BLE001 — an unreadable stem just means "unknown"
            continue
        if hz is not None:
            pitches.append(hz)
    return float(np.median(pitches)) if pitches else None


async def _speaker_kind(db: AsyncSession, speaker: Speaker) -> Optional[str]:
    # What Gemini heard comes first (stored by pipeline_common.ensure_speakers):
    # on a real drama it named 17 of 19 speakers' genders right over three runs,
    # the pitch 1 of 6 (excited young men speak at 230–250 Hz). Pitch is only
    # the fallback.
    if speaker.gender in (Gender.MALE, Gender.FEMALE):
        if speaker.age_group == AgeGroup.CHILD:
            return "child"
        return "man" if speaker.gender == Gender.MALE else "woman"
    rows = (await db.execute(
        select(Segment, Job).join(Job, Job.id == Segment.job_id)
        .where(Segment.speaker_id == speaker.id)
        .order_by((Segment.end_time - Segment.start_time).desc())
        .limit(MAX_LINES)
    )).all()
    spans = [(_separated_vocals(job), seg.start_time, seg.end_time) for seg, job in rows]
    hz = await asyncio.to_thread(_pitch_of_spans, [s for s in spans if s[0]])
    return kind_from_pitch(hz)


@functools.lru_cache(maxsize=1)
def _retired() -> frozenset:
    return frozenset(load_manifest().get("retired", []))


def _ranked(voices: list[Voice], kind: Optional[str]) -> list[Voice]:
    """Candidates of this kind, best first: by section, then every character's first take before second takes."""
    section = {v.id: voice_section(v.gender, v.age_group, v.roles) for v in voices}

    def of(sections: tuple) -> list[Voice]:
        chosen = [v for v in voices if section[v.id] in sections]
        return sorted(chosen, key=lambda v: (sections.index(section[v.id]), v.id.rsplit("-", 1)[-1], v.id))

    if kind is not None:
        return of(SECTIONS[kind])
    men, women = of(SECTIONS["man"]), of(SECTIONS["woman"])     # unknown: alternate
    return [v for pair in zip(men, women) for v in pair] + men[len(women):] + women[len(men):]


async def _pick(db: AsyncSession, kind: Optional[str], taken: Counter) -> Optional[Voice]:
    """The best candidate nobody has, else the least-used one."""
    voices = (await db.execute(select(Voice).where(Voice.id.like(f"{PREFIX}%")))).scalars().all()
    ranked = _ranked([v for v in voices if v.id not in _retired()], kind)
    return min(ranked, key=lambda v: (taken.get(v.id, 0), ranked.index(v))) if ranked else None


async def _taken(db: AsyncSession, speaker: Speaker, job_id: str) -> Counter:
    """Voices used by the episode's other speakers, plus every voice the user chose in the project."""
    in_episode = select(Segment.speaker_id).where(Segment.job_id == job_id, Segment.speaker_id.is_not(None))
    rows = await db.execute(select(Speaker.voice_id).where(
        Speaker.project_id == speaker.project_id, Speaker.id != speaker.id, Speaker.voice_id.is_not(None),
        or_(Speaker.id.in_(in_episode), Speaker.voice_auto.is_not(True)),
    ))
    return Counter(rows.scalars().all())


async def ensure_speaker_voice(db: AsyncSession, speaker: Speaker, job_id: str) -> Optional[Voice]:
    """
    The speaker's voice, picking and saving a built-in one if it has none.
    Commits (callers have nothing pending): first to read the latest state —
    another line may have just picked — then to save the pick.
    """
    async with _assigning():
        await db.commit()                    # fresh transaction: see a pick committed meanwhile even under snapshot isolation
        await db.refresh(speaker)
        if speaker.voice_id:
            return await db.get(Voice, speaker.voice_id)
        kind = await _speaker_kind(db, speaker)
        voice = await _pick(db, kind, await _taken(db, speaker, job_id))
        if not voice:
            return None
        # Only if still unset: another process, or the user, may have just set one
        await db.execute(update(Speaker)
                         .where(Speaker.id == speaker.id, Speaker.voice_id.is_(None))
                         .values(voice_id=voice.id, voice_auto=True))
        await db.commit()
        await db.refresh(speaker)
        if speaker.voice_id == voice.id:
            logger.info(f"Speaker {speaker.display_name or speaker.label} had no voice: "
                        f"picked {voice.name} ({kind or 'unknown'} by pitch)")
        return await db.get(Voice, speaker.voice_id) if speaker.voice_id else None


async def voice_for_line(db: AsyncSession, seg: Segment) -> Optional[Voice]:
    """A fixed built-in voice for a line with no speaker, by the line's own pitch."""
    path = _separated_vocals(await db.get(Job, seg.job_id))
    hz = await asyncio.to_thread(_pitch_of_spans, [(path, seg.start_time, seg.end_time)]) if path else None
    return await _pick(db, kind_from_pitch(hz), Counter())
