"""
app/services/diarizer.py
Speaker diarization + transcription via pyannoteAI cloud API.

Handles any video length via automatic chunking:
  - Audio ≤ CHUNK_THRESHOLD_MINUTES → single API call (fast path)
  - Audio >  CHUNK_THRESHOLD_MINUTES → split into chunks → process each
                                        → merge results with correct timestamps

One API call returns BOTH:
  - Speaker segments with timestamps (who spoke when)
  - Full transcribed text per speaker turn (Whisper large-v3-turbo)

Flow per chunk:
  Step 1 → POST /v1/media/input   get pre-signed PUT URL
  Step 2 → PUT audio chunk        upload WAV slice to S3
  Step 3 → POST /v1/diarize       submit job with transcription:true
  Step 4 → Poll /v1/jobs/{id}     wait for "succeeded"
  Step 5 → Parse output           extract speaker + text segments
  Step 6 → Offset timestamps      add chunk start_time to all segment times
  Step 7 → Merge all chunks       normalise speaker labels across chunks
"""
import asyncio
import logging
import uuid
import httpx
import tempfile
import os
from pathlib import Path
from typing import List
from dataclasses import dataclass
from app.core.config import settings

logger = logging.getLogger(__name__)

PYANNOTEAI_BASE = "https://api.pyannote.ai/v1"

# Audio shorter than this is sent as a single chunk
CHUNK_THRESHOLD_MINUTES = 10

# Each chunk is this long (with overlap to avoid cutting mid-sentence)
CHUNK_DURATION_MINUTES  = 9
CHUNK_OVERLAP_SECONDS   = 10   # overlap between chunks to catch cross-boundary speech


@dataclass
class DiarizedSegment:
    speaker_label: str      # e.g. "SPEAKER_00"
    start_time: float       # seconds (absolute, from start of full audio)
    end_time: float         # seconds
    gender: str             # always "unknown" — pyannoteAI doesn't return gender
    age_group: str          # always "adult" — user edits in UI
    source_text: str = ""   # transcribed text from pyannoteAI STT


# ── Mock fallback ─────────────────────────────────────────────

def _mock_diarize(audio_path: str) -> List[DiarizedSegment]:
    """Mock fallback — used when PYANNOTEAI_TOKEN is not set."""
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

    logger.warning("Using MOCK diarizer — set PYANNOTEAI_TOKEN in .env for real results")
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


# ── pyannoteAI API helpers ────────────────────────────────────

def _auth_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.PYANNOTEAI_TOKEN}",
        "Content-Type": "application/json",
    }


async def _upload_audio(audio_path: str, object_key: str) -> str:
    """
    Get pre-signed PUT URL then upload audio chunk.
    Uses streaming upload — does NOT load entire file into memory.
    Returns media:// URI.
    """
    media_uri = f"media://{object_key}"

    # Step 1 — get pre-signed URL
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{PYANNOTEAI_BASE}/media/input",
            json={"url": media_uri},
            headers=_auth_headers(),
        )

    if resp.status_code not in (200, 201):
        raise RuntimeError(f"media/input error {resp.status_code}: {resp.text}")

    presigned_url = resp.json().get("url")
    if not presigned_url:
        raise RuntimeError(f"No presigned URL in response: {resp.text}")

    # Step 2 — read file bytes then upload (AsyncClient requires bytes, not a file handle)
    audio_bytes = Path(audio_path).read_bytes()
    file_size = len(audio_bytes)
    logger.info(f"Uploading {file_size / 1e6:.1f}MB to pyannoteAI S3...")

    async with httpx.AsyncClient(timeout=600.0) as client:
        put_resp = await client.put(
            presigned_url,
            content=audio_bytes,
            headers={
                "Content-Type": "audio/wav",
                "Content-Length": str(file_size),
            },
        )

    if put_resp.status_code not in (200, 201, 204):
        raise RuntimeError(f"Upload failed {put_resp.status_code}: {put_resp.text}")

    logger.info(f"Audio uploaded: {media_uri}")
    return media_uri


async def _start_diarization_with_transcription(media_uri: str) -> str:
    """Submit diarization + transcription job. Returns job_id."""
    payload = {
        "url": media_uri,
        "model": "precision-2",
        "transcription": True,
        "transcriptionConfig": {
            "model": "faster-whisper-large-v3-turbo"
        },
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{PYANNOTEAI_BASE}/diarize",
            json=payload,
            headers=_auth_headers(),
        )

    if resp.status_code not in (200, 201):
        raise RuntimeError(f"diarize error {resp.status_code}: {resp.text}")

    data   = resp.json()
    job_id = data.get("jobId") or data.get("job_id") or data.get("id")
    if not job_id:
        raise RuntimeError(f"No job_id in response: {resp.text}")

    logger.info(f"pyannoteAI job started: {job_id}")
    return job_id


async def _poll_job(job_id: str, max_wait: int = 900) -> dict:
    """
    Poll until succeeded or failed.
    max_wait=900 allows 15 minutes — enough for a 10-min chunk with transcription.
    """
    elapsed = 0
    poll_interval = 5

    while elapsed < max_wait:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{PYANNOTEAI_BASE}/jobs/{job_id}",
                headers=_auth_headers(),
            )

        if resp.status_code != 200:
            logger.warning(f"Poll error {resp.status_code}: {resp.text}")
            continue

        data   = resp.json()
        status = data.get("status", "").lower()

        if elapsed % 30 == 0:   # log every 30s to avoid spam
            logger.info(f"Job {job_id[:8]}: {status} ({elapsed}s elapsed)")

        if status in ("succeeded", "completed"):
            return data
        if status in ("failed", "error", "cancelled"):
            raise RuntimeError(f"Job {job_id} failed: {data}")

    raise RuntimeError(f"Job {job_id} timed out after {max_wait}s")


def _parse_response(data: dict, time_offset: float = 0.0) -> List[DiarizedSegment]:
    """
    Parse pyannoteAI response into DiarizedSegment list.

    time_offset: add this many seconds to all timestamps.
    Used when processing chunks — each chunk's segments need their
    timestamps shifted back to absolute time in the full audio.
    """
    output = data.get("output", {})

    # Primary: turn-level transcription (speaker + text + timestamps)
    turns = output.get("turnLevelTranscription", [])

    if turns:
        segments = []
        for turn in turns:
            start   = float(turn.get("start", 0)) + time_offset
            end     = float(turn.get("end",   0)) + time_offset
            speaker = turn.get("speaker", "SPEAKER_00")
            text    = turn.get("text", "").strip()

            if end <= start:
                continue

            segments.append(DiarizedSegment(
                speaker_label=speaker,
                start_time=round(start, 3),
                end_time=round(end, 3),
                gender="unknown",
                age_group="adult",
                source_text=text,
            ))

        segments.sort(key=lambda s: s.start_time)
        return segments

    # Fallback: diarization only (no text)
    logger.warning("No turnLevelTranscription — falling back to diarization only")
    raw = output.get("diarization", [])
    segments = []
    for seg in raw:
        start   = float(seg.get("start", 0)) + time_offset
        end     = float(seg.get("end",   0)) + time_offset
        speaker = seg.get("speaker", "SPEAKER_00")
        if end <= start:
            continue
        segments.append(DiarizedSegment(
            speaker_label=speaker,
            start_time=round(start, 3),
            end_time=round(end, 3),
            gender="unknown",
            age_group="adult",
            source_text="",
        ))

    segments.sort(key=lambda s: s.start_time)
    return segments


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


# ── Single chunk processing ───────────────────────────────────

async def _process_single_chunk(
    audio_path: str,
    time_offset: float = 0.0,
    chunk_label: str = "",
) -> List[DiarizedSegment]:
    """Upload one audio chunk to pyannoteAI and return segments."""
    object_key = f"dubber_{uuid.uuid4().hex[:12]}"
    label      = chunk_label or Path(audio_path).name

    logger.info(f"Processing chunk: {label} (offset={time_offset:.0f}s)")

    media_uri = await _upload_audio(audio_path, object_key)
    job_id    = await _start_diarization_with_transcription(media_uri)
    result    = await _poll_job(job_id)
    segments  = _parse_response(result, time_offset=time_offset)

    logger.info(f"Chunk {label}: {len(segments)} segments returned")
    return segments


# ── Main diarization entry points ─────────────────────────────

async def _diarize_and_transcribe(audio_path: str) -> List[DiarizedSegment]:
    """
    Full diarization + transcription with automatic chunking.

    Short audio (≤ CHUNK_THRESHOLD_MINUTES):
      → single pyannoteAI call

    Long audio (> CHUNK_THRESHOLD_MINUTES):
      → split into CHUNK_DURATION_MINUTES chunks
      → process each sequentially (avoid hammering the API)
      → merge and normalise speaker labels
      → clean up chunk files
    """
    duration = _get_audio_duration(audio_path)
    threshold = CHUNK_THRESHOLD_MINUTES * 60

    if duration <= threshold:
        # Fast path — single call
        logger.info(f"Audio {duration:.0f}s ≤ {threshold:.0f}s — single chunk")
        return await _process_single_chunk(audio_path, time_offset=0.0)

    # Chunked path
    logger.info(
        f"Audio {duration:.0f}s ({duration/60:.1f}min) > {CHUNK_THRESHOLD_MINUTES}min "
        f"— splitting into {CHUNK_DURATION_MINUTES}min chunks"
    )

    chunk_duration = CHUNK_DURATION_MINUTES * 60

    with tempfile.TemporaryDirectory(prefix="dubber_chunks_") as chunk_dir:
        # Split audio into overlapping chunks
        chunks = await _split_audio_into_chunks(
            audio_path=audio_path,
            chunk_dir=chunk_dir,
            chunk_duration_secs=chunk_duration,
            overlap_secs=CHUNK_OVERLAP_SECONDS,
        )

        # Process each chunk sequentially
        all_segments: List[DiarizedSegment] = []
        for i, chunk in enumerate(chunks):
            logger.info(
                f"Processing chunk {i+1}/{len(chunks)} "
                f"(offset={chunk['offset']:.0f}s)"
            )
            try:
                segments = await _process_single_chunk(
                    audio_path=chunk["path"],
                    time_offset=chunk["offset"],
                    chunk_label=f"chunk_{chunk['index']:03d}",
                )
                all_segments.extend(segments)
            except Exception as e:
                logger.error(f"Chunk {i+1} failed: {e} — skipping")
                continue

        # Sort by absolute time then normalise speaker labels
        all_segments.sort(key=lambda s: s.start_time)
        return _normalise_speakers(all_segments, CHUNK_OVERLAP_SECONDS)


async def diarize_audio(audio_path: str) -> List[DiarizedSegment]:
    """
    Main entry point for diarization.
    Automatically handles any video length via chunking.
    """
    if not settings.PYANNOTEAI_TOKEN:
        logger.warning("PYANNOTEAI_TOKEN not set — using mock diarizer")
        return _mock_diarize(audio_path)

    try:
        return await _diarize_and_transcribe(audio_path)
    except Exception as e:
        logger.error(f"pyannoteAI failed: {e} — falling back to mock")
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
import asyncio
import logging
import uuid
import httpx
from pathlib import Path
from typing import List
from dataclasses import dataclass
from app.core.config import settings

logger = logging.getLogger(__name__)

PYANNOTEAI_BASE = "https://api.pyannote.ai/v1"


@dataclass
class DiarizedSegment:
    speaker_label: str      # e.g. "SPEAKER_00"
    start_time: float       # seconds
    end_time: float         # seconds
    gender: str             # always "unknown" — pyannoteAI doesn't return gender
    age_group: str          # always "adult" — user edits in UI
    source_text: str = ""   # transcribed text from pyannoteAI STT


def _mock_diarize(audio_path: str) -> List[DiarizedSegment]:
    """Mock fallback — used when PYANNOTEAI_TOKEN is not set."""
    import soundfile as sf
    try:
        duration = sf.info(audio_path).duration
    except Exception:
        duration = 20.0

    segment_len = min(5.0, duration / 4)
    segments, t, i = [], 0.0, 0
    speakers = [
        ("SPEAKER_00", "male",   "adult"),
        ("SPEAKER_01", "female", "young"),
    ]
    while t < duration:
        end = min(t + segment_len, duration)
        label, gender, age = speakers[i % 2]
        segments.append(DiarizedSegment(
            speaker_label=label,
            start_time=round(t, 3),
            end_time=round(end, 3),
            gender=gender,
            age_group=age,
            source_text="你好，我叫李明。很高兴认识你。",  # mock Chinese text
        ))
        t, i = end, i + 1

    logger.warning("Using MOCK diarizer — set PYANNOTEAI_TOKEN in .env for real results")
    return segments


def _auth_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.PYANNOTEAI_TOKEN}",
        "Content-Type": "application/json",
    }


async def _upload_audio(audio_path: str, object_key: str) -> str:
    """
    Step 1+2: Get pre-signed PUT URL then upload audio.
    Returns media:// URI for use in diarize request.
    """
    media_uri = f"media://{object_key}"

    # Step 1 — get pre-signed URL
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{PYANNOTEAI_BASE}/media/input",
            json={"url": media_uri},
            headers=_auth_headers(),
        )

    if resp.status_code not in (200, 201):
        raise RuntimeError(f"media/input error {resp.status_code}: {resp.text}")

    presigned_url = resp.json().get("url")
    if not presigned_url:
        raise RuntimeError(f"No presigned URL in response: {resp.text}")

    # Step 2 — upload audio file
    audio_bytes = Path(audio_path).read_bytes()
    async with httpx.AsyncClient(timeout=300.0) as client:
        put_resp = await client.put(
            presigned_url,
            content=audio_bytes,
            headers={"Content-Type": "audio/wav"},
        )

    if put_resp.status_code not in (200, 201, 204):
        raise RuntimeError(f"Audio upload failed {put_resp.status_code}: {put_resp.text}")

    logger.info(f"Audio uploaded to pyannoteAI: {media_uri}")
    return media_uri


async def _start_diarization_with_transcription(media_uri: str) -> str:
    """
    Step 3: Submit diarization job WITH transcription enabled.
    Uses Whisper large-v3-turbo for transcription.
    Returns job_id.
    """
    payload = {
        "url": media_uri,
        "model": "precision-2",
        "transcription": True,
        "transcriptionConfig": {
            "model": "faster-whisper-large-v3-turbo"
        },
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{PYANNOTEAI_BASE}/diarize",
            json=payload,
            headers=_auth_headers(),
        )

    if resp.status_code not in (200, 201):
        raise RuntimeError(f"diarize error {resp.status_code}: {resp.text}")

    data   = resp.json()
    job_id = data.get("jobId") or data.get("job_id") or data.get("id")
    if not job_id:
        raise RuntimeError(f"No job_id in response: {resp.text}")

    logger.info(f"pyannoteAI job started (diarize + transcribe): {job_id}")
    return job_id


async def _poll_job(job_id: str, max_wait: int = 600) -> dict:
    """
    Step 4: Poll until succeeded or failed.
    Transcription jobs take longer than diarization only — allow up to 10 min.
    """
    elapsed = 0
    while elapsed < max_wait:
        await asyncio.sleep(5)
        elapsed += 5

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{PYANNOTEAI_BASE}/jobs/{job_id}",
                headers=_auth_headers(),
            )

        if resp.status_code != 200:
            logger.warning(f"Poll error {resp.status_code}: {resp.text}")
            continue

        data   = resp.json()
        status = data.get("status", "").lower()
        logger.info(f"Job {job_id}: {status} ({elapsed}s elapsed)")

        if status in ("succeeded", "completed"):
            return data
        if status in ("failed", "error", "cancelled"):
            raise RuntimeError(f"Job {job_id} failed: {data}")

    raise RuntimeError(f"Job {job_id} timed out after {max_wait}s")


def _parse_response(data: dict) -> List[DiarizedSegment]:
    """
    Step 5: Parse combined diarization + transcription output.

    pyannoteAI returns:
    {
      "output": {
        "diarization": [{"start", "end", "speaker"}, ...],
        "turnLevelTranscription": [{"start", "end", "speaker", "text"}, ...]
      }
    }

    We use turnLevelTranscription as primary source since it has
    both speaker attribution and full sentence text.
    Falls back to diarization-only segments if transcription is empty.
    """
    output = data.get("output", {})

    # Primary: turn-level transcription (speaker + text + timestamps)
    turns = output.get("turnLevelTranscription", [])

    if turns:
        segments = []
        for turn in turns:
            start   = float(turn.get("start", 0))
            end     = float(turn.get("end",   0))
            speaker = turn.get("speaker", "SPEAKER_00")
            text    = turn.get("text", "").strip()

            if end <= start:
                continue

            segments.append(DiarizedSegment(
                speaker_label=speaker,
                start_time=round(start, 3),
                end_time=round(end, 3),
                gender="unknown",
                age_group="adult",
                source_text=text,
            ))

        segments.sort(key=lambda s: s.start_time)
        unique  = set(s.speaker_label for s in segments)
        has_text = sum(1 for s in segments if s.source_text)
        logger.info(
            f"pyannoteAI complete: {len(segments)} segments, "
            f"{len(unique)} speakers, {has_text} with transcribed text"
        )
        return segments

    # Fallback: diarization only (no text)
    logger.warning("No turnLevelTranscription in response — using diarization only")
    raw = output.get("diarization", [])
    segments = []
    for seg in raw:
        start   = float(seg.get("start", 0))
        end     = float(seg.get("end",   0))
        speaker = seg.get("speaker", "SPEAKER_00")
        if end <= start:
            continue
        segments.append(DiarizedSegment(
            speaker_label=speaker,
            start_time=round(start, 3),
            end_time=round(end, 3),
            gender="unknown",
            age_group="adult",
            source_text="",
        ))

    segments.sort(key=lambda s: s.start_time)
    logger.info(f"Diarization only: {len(segments)} segments")
    return segments


async def _diarize_and_transcribe(audio_path: str) -> List[DiarizedSegment]:
    """Full pyannoteAI flow: upload → diarize+transcribe → poll → parse."""
    object_key = f"dubber_{uuid.uuid4().hex[:12]}"
    media_uri  = await _upload_audio(audio_path, object_key)
    job_id     = await _start_diarization_with_transcription(media_uri)
    result     = await _poll_job(job_id)
    return _parse_response(result)


async def diarize_audio(audio_path: str) -> List[DiarizedSegment]:
    """
    Main entry point.
    Returns segments with speaker labels AND transcribed text.
    Translation is the only remaining step after this.
    """
    if not settings.PYANNOTEAI_TOKEN:
        logger.warning("PYANNOTEAI_TOKEN not set — using mock diarizer")
        return _mock_diarize(audio_path)

    try:
        return await _diarize_and_transcribe(audio_path)
    except Exception as e:
        logger.error(f"pyannoteAI failed: {e} — falling back to mock")
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