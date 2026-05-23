"""
app/services/transcriber.py
Speech-to-text via HuggingFace Inference API (Whisper large-v3).

Zero model downloads — HuggingFace runs Whisper on their servers.
Requires HF_TOKEN in .env (free at huggingface.co/settings/tokens).

Falls back to mock automatically if token is missing.
"""
import asyncio
import logging
import httpx
import base64
from pathlib import Path
from typing import List
from dataclasses import dataclass
from app.core.config import settings
from app.services.diarizer import DiarizedSegment

logger = logging.getLogger(__name__)

# HuggingFace Inference API endpoint for Whisper large-v3
HF_WHISPER_URL = (
    "https://api-inference.huggingface.co/models/openai/whisper-large-v3"
)


@dataclass
class TranscribedSegment:
    speaker_label: str
    start_time: float
    end_time: float
    gender: str
    age_group: str
    source_text: str    # transcribed text (Chinese)


def _mock_transcribe(segments: List[DiarizedSegment]) -> List[TranscribedSegment]:
    """Fallback mock — sample Chinese text for testing without HF token."""
    logger.warning("Using MOCK transcriber — set HF_TOKEN in .env for real ASR")
    mock_texts = [
        "你好，我叫李明。很高兴认识你。",
        "我也很高兴认识你。你从哪里来？",
        "我从北京来。你呢？",
        "我从上海来。北京很漂亮。",
    ]
    return [
        TranscribedSegment(
            speaker_label=seg.speaker_label,
            start_time=seg.start_time,
            end_time=seg.end_time,
            gender=seg.gender,
            age_group=seg.age_group,
            source_text=mock_texts[i % len(mock_texts)],
        )
        for i, seg in enumerate(segments)
    ]


async def _transcribe_segment_via_hf(
    audio_bytes: bytes,
    source_lang: str,
) -> str:
    """
    Send a single audio chunk to HuggingFace Inference API.
    Returns transcribed text string.
    """
    headers = {
        "Authorization": f"Bearer {settings.HF_TOKEN}",
        "Content-Type": "audio/wav",
    }

    # Language hint tells Whisper what language to expect
    lang_map = {
        "zh": "chinese", "en": "english", "ko": "korean",
        "ja": "japanese", "fr": "french",  "de": "german",
        "vi": "vietnamese", "th": "thai",
    }
    language = lang_map.get(source_lang, source_lang)

    params = {"language": language, "task": "transcribe"}

    # Explicit transport settings fix DNS issues in Mac background tasks
    transport = httpx.AsyncHTTPTransport(retries=2)

    async with httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(60.0, connect=15.0),
    ) as client:
        response = await client.post(
            HF_WHISPER_URL,
            content=audio_bytes,
            headers=headers,
            params=params,
        )

    # HF returns 503 when model is loading — wait and retry
    if response.status_code == 503:
        wait = response.json().get("estimated_time", 20)
        logger.info(f"HF model loading, waiting {wait:.0f}s...")
        await asyncio.sleep(min(wait, 30))
        transport = httpx.AsyncHTTPTransport(retries=2)
        async with httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(60.0, connect=15.0),
        ) as client:
            response = await client.post(
                HF_WHISPER_URL,
                content=audio_bytes,
                headers=headers,
                params=params,
            )

    if response.status_code != 200:
        raise RuntimeError(
            f"HF Whisper API error {response.status_code}: {response.text[:200]}"
        )

    data = response.json()

    # HF Whisper returns {"text": "..."}
    return data.get("text", "").strip()


def _slice_audio_bytes(audio_path: str, start: float, end: float) -> bytes:
    """
    Slice a WAV file to the given time range and return raw bytes.
    Uses soundfile — no torch/torchaudio needed.
    """
    import soundfile as sf
    import io
    import numpy as np

    audio_array, sample_rate = sf.read(audio_path, dtype="float32")

    # Mono conversion if stereo
    if audio_array.ndim > 1:
        audio_array = audio_array.mean(axis=1)

    start_frame = int(start * sample_rate)
    end_frame   = int(end   * sample_rate)
    chunk = audio_array[start_frame:end_frame]

    if len(chunk) == 0:
        return b""

    # Write slice to in-memory WAV bytes
    buffer = io.BytesIO()
    sf.write(buffer, chunk, sample_rate, format="WAV")
    buffer.seek(0)
    return buffer.read()


async def _transcribe_via_hf_api(
    audio_path: str,
    segments: List[DiarizedSegment],
    source_lang: str,
) -> List[TranscribedSegment]:
    """
    Transcribe all segments via HuggingFace Inference API.
    Sends each segment as a WAV chunk — max 3 concurrent requests.
    """
    sem = asyncio.Semaphore(3)   # respect HF rate limits

    async def transcribe_one(seg: DiarizedSegment) -> TranscribedSegment:
        async with sem:
            try:
                audio_bytes = _slice_audio_bytes(
                    audio_path, seg.start_time, seg.end_time
                )

                if not audio_bytes:
                    text = ""
                else:
                    text = await _transcribe_segment_via_hf(audio_bytes, source_lang)

                logger.debug(
                    f"[{seg.start_time:.1f}s] {seg.speaker_label}: {text[:60]}..."
                )

            except Exception as e:
                logger.warning(f"Segment [{seg.start_time:.1f}s] failed: {e}")
                text = ""

            return TranscribedSegment(
                speaker_label=seg.speaker_label,
                start_time=seg.start_time,
                end_time=seg.end_time,
                gender=seg.gender,
                age_group=seg.age_group,
                source_text=text,
            )

    tasks = [transcribe_one(seg) for seg in segments]
    results = await asyncio.gather(*tasks)

    ok = sum(1 for r in results if r.source_text)
    logger.info(f"Transcription complete: {ok}/{len(results)} segments have text")
    return list(results)


async def _transcribe_via_remote_api(
    audio_path: str,
    segments: List[DiarizedSegment],
    source_lang: str,
) -> List[TranscribedSegment]:
    """
    Call your own Whisper GPU server on Lightning AI.
    Used when WHISPER_API_URL is set — faster than HF free tier.
    Falls back to HF API on failure.
    """
    logger.info(f"Calling remote Whisper server: {settings.WHISPER_API_URL}")
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            with open(audio_path, "rb") as f:
                response = await client.post(
                    f"{settings.WHISPER_API_URL}/transcribe",
                    files={"audio": ("audio.wav", f, "audio/wav")},
                    data={"language": source_lang},
                )
        response.raise_for_status()
        data = response.json()

        results = []
        for seg in segments:
            best_text = ""
            for w in data.get("segments", []):
                overlap_start = max(seg.start_time, w["start"])
                overlap_end   = min(seg.end_time,   w["end"])
                if overlap_end > overlap_start:
                    best_text += w["text"].strip() + " "
            results.append(TranscribedSegment(
                speaker_label=seg.speaker_label,
                start_time=seg.start_time,
                end_time=seg.end_time,
                gender=seg.gender,
                age_group=seg.age_group,
                source_text=best_text.strip(),
            ))
        return results

    except Exception as e:
        logger.warning(f"Remote Whisper failed ({e}) — falling back to HF API")
        return await _transcribe_via_hf_api(audio_path, segments, source_lang)


async def transcribe_segments(
    audio_path: str,
    diarized_segments: List[DiarizedSegment],
    source_lang: str = "zh",
) -> List[TranscribedSegment]:
    """
    Main entry point: transcribe all diarized segments.

    Routes to:
    1. Your own Lightning AI Whisper server  (if WHISPER_API_URL is set)
    2. HuggingFace Inference API             (if HF_TOKEN is set)
    3. Mock                                  (fallback)
    """
    if not diarized_segments:
        return []

    # Priority 1: your own GPU server
    if settings.WHISPER_API_URL:
        return await _transcribe_via_remote_api(
            audio_path, diarized_segments, source_lang
        )

    # Priority 2: HuggingFace Inference API
    if settings.HF_TOKEN:
        return await _transcribe_via_hf_api(
            audio_path, diarized_segments, source_lang
        )

    # Priority 3: mock
    logger.warning("No HF_TOKEN or WHISPER_API_URL — using mock transcriber")
    return _mock_transcribe(diarized_segments)