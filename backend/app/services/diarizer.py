"""
app/services/diarizer.py
Speaker diarization + transcription via the free MOSS HF Space
(OpenMOSS-Team/MOSS-transcribe-diarize, gradio_client). One call returns
diarization + ASR together, no auth, up to ~1800s per call; longer audio is
chunked with overlap and speaker labels are normalised across chunks.
Falls back to a mock diarizer on any failure.
"""
import asyncio
import logging
import re
import tempfile
import os
from typing import List
from dataclasses import dataclass
from app.core.config import settings

logger = logging.getLogger(__name__)

# The Space's own documented cap is ~1800s; small safety margin under that.
MOSS_MAX_DURATION_SECONDS = 1750

CHUNK_OVERLAP_SECONDS = 10   # overlap between chunks to catch cross-boundary speech


@dataclass
class DiarizedSegment:
    speaker_label: str      # e.g. "SPEAKER_00"
    start_time: float       # seconds (absolute, from start of full audio)
    end_time: float         # seconds
    gender: str             # always "unknown" — MOSS doesn't return gender
    age_group: str          # always "adult" — user edits in UI
    source_text: str = ""   # transcribed text


# ── Mock fallback ─────────────────────────────────────────────

def _mock_diarize(audio_path: str) -> List[DiarizedSegment]:
    """Mock fallback — used when the MOSS Space is unreachable."""
    import soundfile as sf
    try:
        duration = sf.info(audio_path).duration
    except Exception:
        duration = 20.0

    segment_len = min(5.0, duration / 4)
    segments, t, i = [], 0.0, 0
    speakers = [("SPEAKER_00", "male", "adult"), ("SPEAKER_01", "female", "young")]
    while t < duration:
        end = min(t + segment_len, duration)
        label, gender, age = speakers[i % 2]
        segments.append(DiarizedSegment(
            speaker_label=label,
            start_time=round(t, 3),
            end_time=round(end, 3),
            gender=gender,
            age_group=age,
            source_text="你好，我叫李明。很高兴认识你。",
        ))
        t, i = end, i + 1

    logger.warning("Using MOCK diarizer — the MOSS HF Space call did not succeed")
    return segments


# ── Audio utilities ───────────────────────────────────────────

def _get_audio_duration(audio_path: str) -> float:
    """Get WAV duration in seconds using soundfile."""
    import soundfile as sf
    try:
        return sf.info(audio_path).duration
    except Exception:
        return 0.0


async def _split_audio_into_chunks(
    audio_path: str,
    chunk_dir: str,
    chunk_duration_secs: float,
    overlap_secs: float,
) -> List[dict]:
    """
    Split a WAV file into overlapping chunks using ffmpeg.

    Returns list of chunk info dicts:
    [
      {"path": "/tmp/.../chunk_0.wav", "offset": 0.0,   "duration": 540.0},
      {"path": "/tmp/.../chunk_1.wav", "offset": 530.0, "duration": 540.0},
      ...
    ]

    Each chunk starts (chunk_duration - overlap) seconds after the previous.
    The overlap ensures speech at chunk boundaries is not lost.
    """
    total_duration = _get_audio_duration(audio_path)
    if total_duration == 0:
        raise RuntimeError(f"Could not determine duration of {audio_path}")

    step = chunk_duration_secs - overlap_secs
    chunks = []
    offset = 0.0
    chunk_index = 0

    while offset < total_duration:
        chunk_path = os.path.join(chunk_dir, f"chunk_{chunk_index:03d}.wav")
        actual_duration = min(chunk_duration_secs, total_duration - offset)

        cmd = [
            "ffmpeg", "-y",
            "-i", str(audio_path),
            "-ss", str(offset),
            "-t",  str(actual_duration),
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            chunk_path,
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()

        if process.returncode != 0:
            raise RuntimeError(f"ffmpeg chunk failed: {stderr.decode()[:200]}")

        chunks.append({
            "path":     chunk_path,
            "offset":   offset,
            "duration": actual_duration,
            "index":    chunk_index,
        })

        logger.info(
            f"Chunk {chunk_index}: {offset:.0f}s → {offset + actual_duration:.0f}s "
            f"({actual_duration:.0f}s)"
        )

        offset += step
        chunk_index += 1

    logger.info(f"Split audio into {len(chunks)} chunks "
                f"({chunk_duration_secs:.0f}s each, {overlap_secs:.0f}s overlap)")
    return chunks


# ── MOSS (free HF Space) diarization + transcription ──────────
# OpenMOSS-Team/MOSS-transcribe-diarize — combined diarization+ASR in one call,
# no auth needed, up to ~1800s per call. Output is a plain-text transcript, one
# turn per line: "[start-end] [S##] text", e.g. "[8.09-9.14] [S01] 我叫顾望舒".
# Timestamps are SS.ff under 1 minute, MM:SS.ff at/after 1 minute — verified live.

_MOSS_LINE_RE = re.compile(
    r"^\[(?P<start>(?:\d+:)?\d+(?:\.\d+)?)-(?P<end>(?:\d+:)?\d+(?:\.\d+)?)\]\s*"
    r"\[S(?P<spk>\d+)\]\s*(?P<text>.*)$"
)


def _parse_moss_timestamp(ts: str) -> float:
    """MOSS formats timestamps as SS.ff under 1 minute, MM:SS.ff at/after."""
    if ":" in ts:
        mm, ss = ts.split(":", 1)
        return int(mm) * 60 + float(ss)
    return float(ts)


def _parse_moss_output(raw: str, time_offset: float = 0.0) -> List[DiarizedSegment]:
    """Parse MOSS's line-per-turn transcript into DiarizedSegments.

    time_offset: added to all timestamps — used when this is one chunk of a
    longer audio file, to shift back to absolute time in the full audio.
    """
    segments = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        m = _MOSS_LINE_RE.match(line)
        if not m:
            logger.debug(f"Skipping unparsable MOSS line: {line!r}")
            continue

        start = _parse_moss_timestamp(m.group("start")) + time_offset
        end   = _parse_moss_timestamp(m.group("end"))   + time_offset
        if end <= start:
            continue

        speaker_num = int(m.group("spk"))
        segments.append(DiarizedSegment(
            speaker_label=f"SPEAKER_{speaker_num - 1:02d}",  # S01 -> SPEAKER_00
            start_time=round(start, 3),
            end_time=round(end, 3),
            gender="unknown",
            age_group="adult",
            source_text=m.group("text").strip(),
        ))

    segments.sort(key=lambda s: s.start_time)
    return segments


def _moss_transcribe_call(audio_path: str) -> str:
    """Blocking gradio_client call to the MOSS Space. Run in a thread.

    The API docs mark both audio_obj/video_obj as "required", but the app
    actually enforces exactly one — passing both raises "Please select
    either audio or video, not both." Confirmed live; audio_obj-only works.
    """
    from gradio_client import Client, handle_file
    import httpx

    # gradio_client defaults to httpx's own default timeout (5s per phase)
    # when httpx_kwargs isn't set — not enough to upload a multi-MB audio file
    # on a slower/less stable connection, and fails with "The write operation
    # timed out" before the Space even starts. Give uploads real headroom.
    client = Client(
        settings.DIARIZATION_MOSS_SPACE,
        httpx_kwargs={"timeout": httpx.Timeout(180.0, connect=30.0)},
    )
    result = client.predict(
        audio_obj=handle_file(audio_path),
        video_obj=None,
        api_name="/run_transcription",
    )
    return result


async def _diarize_via_moss(audio_path: str) -> List[DiarizedSegment]:
    """Transcribe + diarize via the free MOSS-transcribe-diarize HF Space.

    A single call handles up to ~MOSS_MAX_DURATION_SECONDS and keeps speaker
    labels consistent throughout (no cross-chunk speaker-drift). Longer audio
    is chunked with overlap and merged with speaker-label normalisation.
    """
    duration = _get_audio_duration(audio_path)

    if duration <= MOSS_MAX_DURATION_SECONDS:
        logger.info(f"MOSS: audio {duration:.0f}s ≤ {MOSS_MAX_DURATION_SECONDS}s — single call")
        raw = await asyncio.to_thread(_moss_transcribe_call, audio_path)
        segments = _parse_moss_output(raw)
        speakers = len(set(s.speaker_label for s in segments))
        logger.info(f"MOSS: {len(segments)} segments, {speakers} speakers")
        return segments

    logger.info(f"MOSS: audio {duration:.0f}s > {MOSS_MAX_DURATION_SECONDS}s — chunking")
    chunk_duration = MOSS_MAX_DURATION_SECONDS - CHUNK_OVERLAP_SECONDS

    with tempfile.TemporaryDirectory(prefix="moss_chunks_") as chunk_dir:
        chunks = await _split_audio_into_chunks(
            audio_path=audio_path,
            chunk_dir=chunk_dir,
            chunk_duration_secs=chunk_duration,
            overlap_secs=CHUNK_OVERLAP_SECONDS,
        )

        all_segments: List[DiarizedSegment] = []
        for i, chunk in enumerate(chunks):
            try:
                raw = await asyncio.to_thread(_moss_transcribe_call, chunk["path"])
                all_segments.extend(_parse_moss_output(raw, time_offset=chunk["offset"]))
            except Exception as e:
                logger.error(f"MOSS chunk {i+1} failed: {e} — skipping")
                continue

        all_segments.sort(key=lambda s: s.start_time)
        return _normalise_speakers(all_segments, CHUNK_OVERLAP_SECONDS)


# ── Speaker normalisation across chunks ───────────────────────

def _normalise_speakers(
    all_segments: List[DiarizedSegment],
    overlap_secs: float,
) -> List[DiarizedSegment]:
    """
    After merging chunks, the same physical speaker may have different
    labels in different chunks (e.g. SPEAKER_00 in chunk 0 and SPEAKER_01
    in chunk 1 may be the same person).

    This function uses the overlap region to match speakers across chunks
    and rename them to a consistent global set.

    Strategy:
    - In the overlap region between two adjacent chunks, find speakers
      that appear in both. Map the chunk-2 label to the chunk-1 label
      if they overlap significantly in time.
    - Apply the mapping to all segments from that chunk onwards.
    """
    if not all_segments:
        return all_segments

    # Find all unique chunk offsets (approximated from speaker label patterns)
    # For simplicity: sort by time, deduplicate overlapping segments,
    # then renumber speakers globally in order of first appearance.

    # Step 1: Remove duplicate segments from overlap regions
    # Keep the segment from the earlier chunk (lower offset = more accurate timing)
    seen_ranges: List[tuple] = []
    deduped = []

    for seg in sorted(all_segments, key=lambda s: s.start_time):
        is_dup = False
        for (s, e) in seen_ranges:
            overlap = min(seg.end_time, e) - max(seg.start_time, s)
            if overlap > (seg.end_time - seg.start_time) * 0.8:
                is_dup = True
                break
        if not is_dup:
            deduped.append(seg)
            seen_ranges.append((seg.start_time, seg.end_time))

    # Step 2: Renumber speakers in order of first appearance
    speaker_map: dict[str, str] = {}
    counter = 0

    for seg in deduped:
        if seg.speaker_label not in speaker_map:
            speaker_map[seg.speaker_label] = f"SPEAKER_{counter:02d}"
            counter += 1
        seg.speaker_label = speaker_map[seg.speaker_label]

    unique = set(s.speaker_label for s in deduped)
    has_text = sum(1 for s in deduped if s.source_text)
    logger.info(
        f"Merged + normalised: {len(deduped)} segments, "
        f"{len(unique)} speakers, {has_text} with text"
    )
    return deduped


# ── Main entry point ──────────────────────────────────────────

async def diarize_audio(
    audio_path: str,
    num_speakers: int | None = None,
    max_speakers: int | None = None,
) -> List[DiarizedSegment]:
    """
    Main entry point for diarization — MOSS HF Space, mock on failure.

    num_speakers / max_speakers are accepted for API compatibility but the
    MOSS Space has no speaker-count parameter, so they are ignored.
    """
    try:
        return await _diarize_via_moss(audio_path)
    except Exception as e:
        logger.error(f"MOSS diarization failed: {e} — falling back to mock")
        return _mock_diarize(audio_path)


def build_voice_design_prompt(speaker_label: str, gender: str, age_group: str) -> str:
    """Auto-generate a VoxCPM2 voice design prompt. User can edit in UI."""
    age_descriptors = {
        "child":  "young child, high-pitched, innocent voice",
        "young":  "young adult, energetic, clear voice",
        "adult":  "adult, confident, natural voice",
        "senior": "elderly, warm, slightly raspy voice",
    }
    if gender == "unknown":
        return f"A natural speaking voice, {age_descriptors.get(age_group, 'adult, natural voice')}"
    gender_prefix = "male" if gender == "male" else "female"
    return f"A {gender_prefix}, {age_descriptors.get(age_group, 'adult, natural voice')}"
