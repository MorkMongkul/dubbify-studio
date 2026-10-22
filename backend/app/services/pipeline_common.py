"""
app/services/pipeline_common.py
Shared helpers used by both the ASR pipeline and the subtitle pipeline:
job status updates, speaker bookkeeping, speaker reference voices, the atomic
segment swap, failure recovery, and orphaned-job recovery on startup.
"""
import logging
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.paths import resolve_existing
from app.models.models import (
    AgeGroup, ChatProposedEdit, Gender, Job, JobStatus, Project, Segment, Speaker, Voice,
)
from app.services import asr, story_notes
from app.services.diarizer import build_voice_design_prompt

logger = logging.getLogger(__name__)


async def update_job_status(
    db: AsyncSession, job: Job, status: JobStatus, progress: int, error_msg: str = ""
) -> None:
    """Persist a job's status/progress and log the transition."""
    job.status    = status
    job.progress  = progress
    job.error_msg = error_msg
    await db.commit()
    logger.info(f"Job {job.id[:8]} | {status.value} | {progress}%")


async def episodes(db: AsyncSession, project_id: str) -> List[Job]:
    """The project's episodes (jobs) in upload order — index+1 is the episode number."""
    result = await db.execute(select(Job).where(Job.project_id == project_id).order_by(Job.created_at))
    return list(result.scalars().all())


async def episode_ids(db: AsyncSession, project_id: str) -> List[str]:
    result = await db.execute(select(Job.id).where(Job.project_id == project_id).order_by(Job.created_at))
    return [row[0] for row in result.all()]


async def translation_story(db: AsyncSession, project_id: str, job_id: str) -> str:
    """
    The project's story notes for translating episode `job_id` (story_notes.
    for_translation), read fresh: they may have learned something during a
    long transcription. Read it before releasing the connection for Gemini.
    """
    notes = (await db.execute(select(Project.story_notes).where(Project.id == project_id))).scalar_one_or_none()
    return story_notes.for_translation(notes, job_id, await episode_ids(db, project_id))


_VOICE_KIND = {"man": "a man", "woman": "a woman", "boy": "a boy", "girl": "a girl"}
# Who a voice sounds like, as Gemini heard it (asr.VOICES) ↔ the speaker's own fields.
_HEARD = {"man": (Gender.MALE, AgeGroup.ADULT), "woman": (Gender.FEMALE, AgeGroup.ADULT),
          "boy": (Gender.MALE, AgeGroup.CHILD), "girl": (Gender.FEMALE, AgeGroup.CHILD)}


def heard_voice_of(speaker: Optional[Speaker]) -> Optional[str]:
    """The voice stored on a speaker (from Gemini's ear) as man/woman/boy/girl, or None."""
    if speaker is None or speaker.gender not in (Gender.MALE, Gender.FEMALE):
        return None
    child = speaker.age_group == AgeGroup.CHILD
    return ("boy" if child else "man") if speaker.gender == Gender.MALE else ("girl" if child else "woman")


async def translation_cast(
    db: AsyncSession, job: Job, labels: Iterable[Optional[str]], heard: Optional[Dict[str, str]] = None,
) -> str:
    """
    Who each speaker tag (labels: those of an episode's lines) is, for the
    translation prompt. Per tag: the name the user gave, the
    Story notes' character linked to it, and man/woman/boy/girl with age from
    the voice the user chose — else "probably …" from what Gemini heard (`heard`,
    this run's transcription, or what is stored on the speaker; 7 of 7 speakers
    right from the original sound). Nothing is guessed from pitch: on a real drama
    the pitch of the lines called four of five men women or children (excited
    young men speak at 230–250 Hz), and a wrong gender is worse than none —
    Gemini then judges from the dialogue. Read it before releasing the
    connection for Gemini.
    """
    tags = sorted({label for label in labels if label})
    if not tags:
        return ""
    speakers = {sp.label: sp for sp in (await db.execute(
        select(Speaker).where(Speaker.project_id == job.project_id, Speaker.label.in_(tags))
    )).scalars().all()}
    voice_ids = [sp.voice_id for sp in speakers.values() if sp.voice_id]
    voices = {v.id: v for v in (await db.execute(select(Voice).where(Voice.id.in_(voice_ids)))).scalars().all()} \
        if voice_ids else {}
    notes = (await db.execute(select(Project.story_notes).where(Project.id == job.project_id))).scalar_one_or_none()
    characters = {c["speaker"]: c for c in story_notes.normalize(notes)["characters"] if c["speaker"]}

    rows = []
    for label in tags:
        speaker, character = speakers.get(label), characters.get(label, {})
        name = (speaker.display_name or "").strip() if speaker else ""
        name = name if name and name != label else character.get("zh", "")
        km = character.get("km", "")
        voice = voices.get(speaker.voice_id) if speaker and speaker.voice_id else None
        if voice is not None and voice.gender in _VOICE_KIND and not speaker.voice_auto:
            kind = _VOICE_KIND[voice.gender] + (f", {voice.age_group}" if voice.age_group else "")
        else:
            voice_heard = (heard or {}).get(label) or heard_voice_of(speaker)
            kind = f"probably {_VOICE_KIND[voice_heard]} (by the voice)" if voice_heard in _VOICE_KIND else "not known"
        who = " ".join(part for part in (name, f"(Khmer: {km})" if km else "") if part)
        rows.append(f"- {label}: " + (f"{who} — {kind}" if who else kind))
    return "WHO IS SPEAKING — the speaker tags in these lines:\n" + "\n".join(rows)


MEMORY_MAX = 3000   # (source, translation) pairs read for one translation run; translator narrows them per batch


async def translation_memory(db: AsyncSession, project_id: str, job_id: str) -> List[tuple]:
    """
    The team's own translations in the project's OTHER episodes, as (source,
    Khmer) pairs, most recent episode first — the examples translator.
    pick_examples chooses from. Every line with both texts counts: a line the
    team corrected and one they kept as it was are both their Khmer. Read it
    before releasing the connection for Gemini.
    """
    rows = (await db.execute(
        select(Segment.source_text, Segment.khmer_text).join(Job, Job.id == Segment.job_id)
        .where(Job.project_id == project_id, Job.id != job_id)
        .order_by(Job.created_at.desc(), Segment.start_time)
        .limit(MEMORY_MAX * 2)
    )).all()
    pairs = [((zh or "").strip(), (km or "").strip()) for zh, km in rows]
    return [(zh, km) for zh, km in pairs if zh and km][:MEMORY_MAX]


def original_sound_for(job: Job) -> Optional[str]:
    """
    The episode's original sound for Gemini to hear: the extracted audio, else
    the video itself (the extracted audio is deleted after separation; clips
    are cut straight from the video). None when neither is on disk. Not the
    separated vocals: the separation service drops a voice's low end, and
    Gemini then took men for women or children (3 of 6 right vs 7 of 7).
    """
    found = resolve_existing(job.audio_path) or resolve_existing(job.video_path)
    return str(found) if found else None


def vocals_path_for(job: Job) -> Optional[str]:
    """The job's separated vocals stem, else its extracted audio — whichever exists on disk."""
    stem = Path(settings.UPLOAD_DIR) / job.project_id / job.id / "vocals.wav"
    if stem.exists():
        return str(stem)
    audio = resolve_existing(job.audio_path)
    return str(audio) if audio else None


# ── Speakers ──────────────────────────────────────────────────────────────────

def is_confirmed_speaker(speaker: Speaker) -> bool:
    """A speaker the user has made their own — renamed it or gave it a voice
    (a voice the app picked by itself, auto_voice.py, doesn't count)."""
    name = (speaker.display_name or "").strip()
    return (bool(speaker.voice_id) and not speaker.voice_auto) or (bool(name) and name != speaker.label)


async def project_speaker_labels(db: AsyncSession, project_id: str) -> set:
    result = await db.execute(select(Speaker.label).where(Speaker.project_id == project_id))
    return set(result.scalars().all())


async def ensure_speakers(
    db: AsyncSession, project_id: str, labels: Iterable[str], heard: Optional[Dict[str, str]] = None,
) -> Dict[str, Speaker]:
    """
    {label: Speaker} for every label, creating the missing ones. Flushes but
    does not commit, so the caller decides what is written atomically.
    Speakers are project-scoped: the same label means the same character
    across the project's episodes. `heard` (label → man/woman/boy/girl, from
    the transcription) is kept on a speaker whose gender isn't known yet —
    "Choose for me" (auto_voice) and the translation use it.
    """
    result = await db.execute(select(Speaker).where(Speaker.project_id == project_id))
    speaker_map: Dict[str, Speaker] = {sp.label: sp for sp in result.scalars().all()}
    for label in labels:
        if label not in speaker_map:
            speaker = Speaker(
                project_id=project_id,
                label=label,
                display_name=label,
                gender=Gender.UNKNOWN,
                age_group=AgeGroup.ADULT,
                voice_design_prompt=build_voice_design_prompt(label, "unknown", "adult"),
            )
            db.add(speaker)
            speaker_map[label] = speaker
    for label, voice in (heard or {}).items():
        speaker = speaker_map.get(label)
        if speaker is not None and voice in _HEARD and speaker.gender in (None, Gender.UNKNOWN):
            speaker.gender, speaker.age_group = _HEARD[voice]
    await db.flush()
    return speaker_map


async def remove_unused_auto_speakers(db: AsyncSession, project_id: str) -> None:
    """Drop speakers nothing points at any more, unless the user named or voiced them."""
    in_use = select(Segment.speaker_id).where(Segment.speaker_id.is_not(None)).distinct()
    result = await db.execute(
        select(Speaker).where(Speaker.project_id == project_id, Speaker.id.not_in(in_use))
    )
    for speaker in result.scalars().all():
        if not is_confirmed_speaker(speaker):
            await db.delete(speaker)
    await db.flush()


REFERENCE_SAID = 8   # other lines of a character shown with its reference voice


async def build_speaker_references(
    db: AsyncSession, project_id: str, tmp_dir: str, exclude_job_id: Optional[str] = None,
) -> List[asr.SpeakerReference]:
    """
    Reference voices for the ASR: one short clip per named/voiced speaker in
    the project (from any episode), so a recurring character keeps the same
    label — and with it the name and voice the user assigned. Each also says
    who it is (the voice the user chose: "a man, 20s") and up to
    REFERENCE_SAID of its lines from episodes other than `exclude_job_id`
    (the one being analysed, whose old lines may be wrongly labelled).
    """
    result = await db.execute(select(Speaker).where(Speaker.project_id == project_id))
    # A voice the app picked (auto_voice.py) counts once lines were voiced with
    # it: the speaker must be recognised again, or it's replaced and re-voiced
    # differently. A pick nobody has heard yet doesn't count.
    heard = set((await db.execute(
        select(Segment.speaker_id).where(Segment.speaker_id.is_not(None), Segment.tts_audio_path.is_not(None),
                                         Segment.tts_audio_path != "")
    )).scalars().all())
    confirmed = {sp.id: sp for sp in result.scalars().all()
                 if is_confirmed_speaker(sp) or (sp.voice_id and sp.voice_auto and sp.id in heard)}
    if not confirmed:
        return []

    rows = (await db.execute(
        select(Segment, Job).join(Job, Job.id == Segment.job_id)
        .where(Segment.speaker_id.in_(list(confirmed)))
    )).all()
    voice_ids = [sp.voice_id for sp in confirmed.values() if sp.voice_id and not sp.voice_auto]
    voices = {v.id: v for v in (await db.execute(select(Voice).where(Voice.id.in_(voice_ids)))).scalars().all()} \
        if voice_ids else {}
    counts: Dict[str, int] = {}
    best: Dict[str, tuple] = {}
    said: Dict[str, List[str]] = {}
    for seg, job in rows:
        counts[seg.speaker_id] = counts.get(seg.speaker_id, 0) + 1
        text = (seg.source_text or "").strip()
        if not text:
            continue
        if job.id != exclude_job_id and text not in said.setdefault(seg.speaker_id, []):
            said[seg.speaker_id].append(text)
        duration = seg.end_time - seg.start_time
        if not asr.REFERENCE_MIN_SECONDS <= duration <= asr.REFERENCE_MAX_SECONDS:
            continue
        if seg.speaker_id not in best or duration > best[seg.speaker_id][0]:
            best[seg.speaker_id] = (duration, seg, job)

    references: List[asr.SpeakerReference] = []
    for speaker_id in sorted(best, key=lambda sid: -counts[sid]):
        duration, seg, job = best[speaker_id]
        source = original_sound_for(job) or vocals_path_for(job)   # as the clips it is compared with
        if not source:
            continue
        clip = os.path.join(tmp_dir, f"ref_{speaker_id}.flac")
        try:
            await asr._slice_chunk(source, seg.start_time, seg.end_time, clip)
        except Exception as e:
            logger.warning(f"Could not cut a reference clip for speaker {confirmed[speaker_id].label}: {e}")
            continue
        voice = voices.get(confirmed[speaker_id].voice_id)
        about = (_VOICE_KIND[voice.gender] + (f", {voice.age_group}" if voice.age_group else "")) \
            if voice is not None and voice.gender in _VOICE_KIND else ""
        lines = tuple(sorted(said.get(speaker_id, []), key=len, reverse=True)[:REFERENCE_SAID])
        references.append(asr.SpeakerReference(confirmed[speaker_id].label, clip, seg.source_text.strip(), duration,
                                               about=about, said=lines))
        if len(references) >= asr.ASR_MAX_REFERENCES:
            break
    return references


# ── Segments ──────────────────────────────────────────────────────────────────

async def replace_job_segments(db: AsyncSession, job_id: str, new_segments: List[Segment]) -> None:
    """
    Swap a job's segments for new ones inside the caller's transaction — the
    old rows (and Assistant edits pointing at them) only disappear when the
    caller commits, so a failure anywhere before that loses nothing.
    """
    old_ids = select(Segment.id).where(Segment.job_id == job_id)
    await db.execute(delete(ChatProposedEdit).where(ChatProposedEdit.segment_id.in_(old_ids)))
    await db.execute(delete(Segment).where(Segment.job_id == job_id))
    db.add_all(new_segments)
    await db.flush()


async def restore_or_fail_job(db: AsyncSession, job_id: str, message: str) -> None:
    """
    After a failed Stage 2 / speaker-detection run: roll back whatever was half
    written, then leave a job that still has segments COMPLETED (so the editor
    stays usable and the message explains what happened) — FAILED otherwise.
    """
    try:
        await db.rollback()
        job = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
        if job is None:
            return
        has_segments = (await db.execute(
            select(Segment.id).where(Segment.job_id == job_id).limit(1)
        )).first() is not None
        if has_segments:
            job.status, job.progress = JobStatus.COMPLETED, 100
            job.error_msg = f"{message} Your existing segments were kept."
        else:
            job.status, job.progress, job.error_msg = JobStatus.FAILED, 0, message
        await db.commit()
    except Exception:
        logger.exception(f"Could not record the failure for job {job_id[:8]}")


# ── Startup recovery ──────────────────────────────────────────────────────────

# Statuses that mean a job was actively being processed in a worker.
# If the server restarts, these can never resume on their own (work runs in
# in-process BackgroundTasks). STEMS_READY is a valid paused state and is
# intentionally excluded.
_ORPHANABLE_STATUSES = [
    JobStatus.PENDING,
    JobStatus.EXTRACTING,
    JobStatus.SEPARATING,
    JobStatus.DIARIZING,
    JobStatus.TRANSCRIBING,
    JobStatus.TRANSLATING,
    JobStatus.SYNTHESIZING,
    JobStatus.MIXING,
]
# A background task is working on the job right now.
RUNNING_STATUSES = frozenset(_ORPHANABLE_STATUSES)


async def recover_orphaned_jobs() -> None:
    """
    Settle any job left mid-processing by a previous run.

    A job that already has segments (an interrupted re-analysis, speaker
    detection, batch TTS or export) goes back to COMPLETED with a note —
    failing it would hide the user's edited timeline. Only jobs with nothing
    to show are marked FAILED.

    Filtering is done in Python rather than via a SQL WHERE on the status enum:
    the live Postgres `jobs.status` column is plain VARCHAR, so an enum-typed
    comparison emits a `::jobstatus` cast that has no matching operator.
    """
    from app.core.database import AsyncSessionLocal

    orphanable = set(_ORPHANABLE_STATUSES)
    async with AsyncSessionLocal() as db:
        jobs = [j for j in (await db.execute(select(Job))).scalars().all() if j.status in orphanable]
        if not jobs:
            return
        ids = [j.id for j in jobs]
        with_segments = set((await db.execute(
            select(Segment.job_id).where(Segment.job_id.in_(ids)).distinct()
        )).scalars().all())
        translated = set((await db.execute(
            select(Segment.job_id).where(Segment.job_id.in_(ids), Segment.khmer_text != "").distinct()
        )).scalars().all())
        for job in jobs:
            job_dir = Path(settings.UPLOAD_DIR) / job.project_id / job.id
            if job.status == JobStatus.SEPARATING and all(
                (job_dir / name).exists() for name in ("vocals.wav", "no_vocals.wav")
            ):
                # Usually "Try again" on separation, which only replaces the
                # stems once it has succeeded: the episode is as it was.
                job.status = JobStatus.COMPLETED if job.id in translated else JobStatus.STEMS_READY
                job.progress = 100 if job.id in translated else 30
                job.error_msg = "Separating music and voices was interrupted. The episode is as it was before."
            elif job.id in with_segments:
                job.status, job.progress = JobStatus.COMPLETED, 100
                job.error_msg = (
                    "Interrupted by a server restart — your segments were kept. "
                    "Re-run the step that was in progress."
                )
            else:
                job.status = JobStatus.FAILED
                job.error_msg = "Interrupted by server restart"
        await db.commit()
        outcome = {}
        for job in jobs:
            outcome[job.status.value] = outcome.get(job.status.value, 0) + 1
        logger.warning(f"Recovered {len(jobs)} interrupted job(s): {outcome}")
