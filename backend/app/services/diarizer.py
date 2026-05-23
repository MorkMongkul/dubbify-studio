"""
app/services/diarizer.py
Speaker diarization + transcription via pyannoteAI cloud API.

One API call returns BOTH:
  - Speaker segments with timestamps (who spoke when)
  - Full transcribed text per speaker turn

This means we do NOT need a separate Whisper/Groq call.
pyannoteAI runs Precision-2 diarization + Whisper large-v3-turbo internally.

Correct flow:
  Step 1 → POST /v1/media/input   get pre-signed PUT URL
  Step 2 → PUT audio file         upload WAV to S3
  Step 3 → POST /v1/diarize       submit job with transcription:true
  Step 4 → Poll /v1/jobs/{id}     wait for "succeeded"
  Step 5 → Parse output           extract speaker + text segments

Requires PYANNOTEAI_TOKEN in .env (free trial at dashboard.pyannote.ai).
Falls back to mock automatically if token is missing.
"""
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
import asyncio
import logging
import uuid
import httpx
from pathlib import Path
from typing import List
from dataclasses import dataclass
from app.core.config import settings

