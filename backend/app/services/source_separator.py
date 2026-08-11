"""
app/services/source_separator.py

Splits audio into Vocals and Background (BGM + SFX) stems via a HuggingFace
Space (BS-RoFormer etc., see SEPARATION_HF_SPACE/SEPARATION_HF_MODEL) — the
compute happens in the cloud, nothing runs locally.

Produces in the job dir:
  vocals.wav      — isolated human voice (used for diarization / ASR)
  no_vocals.wav   — everything else: music, drums, bass, SFX (BGM track)
  *.preview.wav   — 8kHz mono copies for fast waveform rendering
  *.m4a           — AAC copies for in-editor playback

Falls back to no-op (returns the original audio path) if the Space fails.
"""
import asyncio
import logging
from pathlib import Path
from typing import Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)


async def separate_vocals_bgm(audio_path: str, output_dir: str) -> Tuple[str, str]:
    """
    Separate audio into vocals.wav and no_vocals.wav in output_dir.

    Returns:
        (vocals_wav_path, bgm_wav_path)
        Returns (audio_path, audio_path) if separation fails.
    """
    audio_path_obj = Path(audio_path)
    output_dir_obj = Path(output_dir)

    vocals_target = output_dir_obj / "vocals.wav"
    bgm_target    = output_dir_obj / "no_vocals.wav"

    # Skip if stems already exist (e.g. re-running a failed job)
    if vocals_target.exists() and bgm_target.exists():
        logger.info("Stem files already present — skipping separation")
        return str(vocals_target), str(bgm_target)

    result = await _separate_via_hf(audio_path_obj, output_dir_obj, vocals_target, bgm_target)
    if result:
        return result

    logger.warning("HF separation failed — pipeline will use original mixed audio")
    return audio_path, audio_path


# ── HuggingFace Space separation (gradio_client) ──────────────

def _hf_separate_call(audio_path: str) -> Tuple[str, str]:
    """Blocking gradio_client call to the vocal-separation Space. Run in a thread.

    Returns the raw (vocals, background) file paths the Space produced — these may
    be MP3/FLAC, so the caller transcodes them to real WAV.
    """
    from gradio_client import Client, handle_file
    import httpx

    token = settings.HF_TOKEN or None
    # gradio_client defaults to httpx's own default timeout (5s per phase) when
    # httpx_kwargs isn't set — nowhere near enough to upload a multi-MB audio
    # file on a slower/less stable connection than a fast broadband line, and
    # fails with "The write operation timed out" well before the Space even
    # starts processing. Give uploads real headroom.
    client = Client(
        settings.SEPARATION_HF_SPACE,
        token=token,
        httpx_kwargs={"timeout": httpx.Timeout(180.0, connect=30.0)},
    )
    # /separate → (vocals_filepath, background_filepath)
    result = client.predict(
        handle_file(audio_path),
        settings.SEPARATION_HF_MODEL,
        api_name="/separate",
    )
    return result[0], result[1]


async def _to_wav(src: str, dest: Path) -> bool:
    """Transcode any audio file to 44.1kHz stereo 16-bit WAV (the Space may return MP3)."""
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le",
        str(dest),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.warning(f"WAV transcode failed for {Path(src).name}: {stderr.decode()[:200]}")
        return False
    return True


async def _separate_via_hf(
    audio_path: Path,
    output_dir: Path,
    vocals_target: Path,
    bgm_target: Path,
) -> Tuple[str, str] | None:
    """Separate via the HF Space (BS-RoFormer etc.) — compute happens in the cloud."""
    logger.info(
        f"Separating via HF Space '{settings.SEPARATION_HF_SPACE}' "
        f"(model={settings.SEPARATION_HF_MODEL}): {audio_path.name}"
    )
    try:
        vocals_src, bgm_src = await asyncio.to_thread(_hf_separate_call, str(audio_path))
    except Exception as e:
        logger.warning(f"HF separation failed: {e}")
        return None

    # The Space returns MP3 — transcode both stems to real WAV for downstream tools
    if not await _to_wav(vocals_src, vocals_target) or not await _to_wav(bgm_src, bgm_target):
        return None

    await _create_preview(vocals_target, output_dir / "vocals.preview.wav")
    await _create_preview(bgm_target,    output_dir / "no_vocals.preview.wav")
    await _create_playback_m4a(vocals_target, output_dir / "vocals.m4a")
    await _create_playback_m4a(bgm_target,    output_dir / "no_vocals.m4a")
    logger.info(f"HF separation done: {vocals_target.name} + {bgm_target.name}")
    return str(vocals_target), str(bgm_target)


async def _create_playback_m4a(source: Path, dest: Path) -> None:
    """
    Encode a stem to AAC for in-editor playback. The editor's <audio> tags
    stream these instead of the raw WAVs — same audibility at ~7% of the bytes.
    The WAV stems remain the source of truth for diarization and the final mix.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", str(source),
        "-c:a", "aac",
        "-b:a", "192k",
        str(dest),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if proc.returncode == 0:
            logger.info(f"Playback stem created: {dest.name}")
        else:
            logger.warning(f"ffmpeg playback encode failed for {source.name} — editor will stream the WAV")
    except Exception as e:
        logger.warning(f"Could not create playback stem for {source.name}: {e}")


async def _create_preview(source: Path, dest: Path) -> None:
    """
    Downsample a WAV stem to 8kHz mono for fast browser waveform decoding.

    Full stems are ~19MB (44.1kHz stereo 110s).
    Preview files are ~1.7MB (8kHz mono 110s) — Web Audio decodes them instantly.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", str(source),
        "-ar", "8000",      # 8kHz — enough resolution for waveform peaks
        "-ac", "1",         # mono
        "-c:a", "pcm_s16le",
        str(dest),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if proc.returncode == 0:
            logger.info(f"Waveform preview created: {dest.name}")
        else:
            logger.warning(f"ffmpeg preview failed for {source.name} — waveform will load slowly")
    except Exception as e:
        logger.warning(f"Could not create waveform preview for {source.name}: {e}")
