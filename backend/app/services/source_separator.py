"""
app/services/source_separator.py

Splits audio into Vocals and Background (BGM + SFX) stems via a HuggingFace
Space (see SEPARATION_HF_SPACE) — the compute happens in the cloud, nothing
runs locally.

Produces in the job dir:
  vocals.wav      — isolated human voice (used for diarization / ASR)
  no_vocals.wav   — everything else: music, drums, bass, SFX (BGM track)
  *.preview.wav   — 8kHz mono copies for fast waveform rendering
  *.m4a           — AAC copies for in-editor playback

Falls back to no-op (returns the original audio path) if the Space fails.
"""
import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Tuple

from app.core.config import settings
from app.core.binaries import ffmpeg

logger = logging.getLogger(__name__)

# Overall wall-clock cap on the blocking HF call. gradio_client's own httpx
# timeout only bounds individual connect/read/write operations — Gradio's job
# queue streams periodic SSE keepalives while queued/processing, so a Space
# that's alive but slow (not dead) can hold the connection open indefinitely
# without ever tripping a per-phase timeout, hanging the pipeline forever with
# the no-op fallback below never getting a chance to run.
# Written next to the stems when separation fell back to the original audio,
# with the reason. The editor shows a lasting warning with "Try again" while
# it exists (JobResponse.stems_separated).
UNAVAILABLE_MARKER = "separation-unavailable.txt"

# The stems and the copies made from them for the waveform and playback.
_STEM_FILES = (
    "vocals.wav", "no_vocals.wav", "vocals.preview.wav", "no_vocals.preview.wav",
    "vocals.m4a", "no_vocals.m4a",
)


def separation_timeout(duration_secs: float) -> float:
    """Upload, queue and processing all grow with the episode's length: 5 min up to 30 min."""
    return min(1800.0, max(300.0, 120.0 + 1.5 * (duration_secs or 0.0)))


async def separate_vocals_bgm(
    audio_path: str, output_dir: str, duration_secs: float = 0.0, force: bool = False,
) -> Tuple[str, str, bool]:
    """
    Separate audio into vocals.wav and no_vocals.wav in output_dir.

    force=True separates again even though stems exist (Try again after a
    failure) — see _separate_again.

    Returns:
        (vocals_wav_path, bgm_wav_path, degraded)
        degraded=True means separation failed and both paths are fallback
        copies of the original mixed audio — see _write_fallback_stems.
    """
    audio_path_obj = Path(audio_path)
    output_dir_obj = Path(output_dir)

    vocals_target = output_dir_obj / "vocals.wav"
    bgm_target    = output_dir_obj / "no_vocals.wav"
    marker        = output_dir_obj / UNAVAILABLE_MARKER

    if force:
        return await _separate_again(audio_path_obj, output_dir_obj, duration_secs)

    # Skip if stems already exist (e.g. re-running a failed job)
    if vocals_target.exists() and bgm_target.exists():
        logger.info("Stem files already present — skipping separation")
        return str(vocals_target), str(bgm_target), marker.exists()

    result, reason = await _separate_via_hf(
        audio_path_obj, output_dir_obj, vocals_target, bgm_target,
        timeout=separation_timeout(duration_secs),
    )
    if result:
        marker.unlink(missing_ok=True)
        return result[0], result[1], False

    logger.warning("HF separation failed — using original mixed audio as both stems")
    vocals_path, bgm_path = await _write_fallback_stems(
        audio_path_obj, output_dir_obj, vocals_target, bgm_target
    )
    marker.write_text(reason + "\n")
    return vocals_path, bgm_path, True


async def _separate_again(audio_path: Path, output_dir: Path, duration_secs: float) -> Tuple[str, str, bool]:
    """
    Try again after separation fell back to the original audio. The new stems
    are made in a temporary folder and swapped in only once they're complete:
    quitting mid-way, or failing again, leaves the episode exactly as it was.
    """
    vocals_target = output_dir / "vocals.wav"
    bgm_target    = output_dir / "no_vocals.wav"
    marker        = output_dir / UNAVAILABLE_MARKER

    with tempfile.TemporaryDirectory(prefix="separating-", dir=output_dir) as tmp:
        work = Path(tmp)
        result, reason = await _separate_via_hf(
            audio_path, work, work / "vocals.wav", work / "no_vocals.wav",
            timeout=separation_timeout(duration_secs),
        )
        if not result:
            if not (vocals_target.exists() and bgm_target.exists()):
                await _write_fallback_stems(audio_path, output_dir, vocals_target, bgm_target)
            marker.write_text(reason + "\n")
            return str(vocals_target), str(bgm_target), True

        for name in _STEM_FILES:
            if (work / name).exists():
                os.replace(work / name, output_dir / name)
            else:  # e.g. a playback copy that failed to encode: the editor streams the WAV
                (output_dir / name).unlink(missing_ok=True)
    # The music level is re-rendered from this file; it still held the old mix.
    (output_dir / BGM_UNTOUCHED).unlink(missing_ok=True)
    marker.unlink(missing_ok=True)
    return str(vocals_target), str(bgm_target), False


# The music & effects stem as separated, before the user's level is applied.
BGM_UNTOUCHED = "no_vocals_raw.wav"


async def render_bgm_volume(job_dir: Path, volume_db: float) -> None:
    """
    Set the music & effects stem's level. Always rendered from the untouched
    stem, so changes never stack, then the waveform and playback copies are
    remade. The export reads no_vocals.wav, so it picks up the change.
    """
    from app.services.audio_extractor import apply_audio_effects

    bgm_wav, raw_wav = job_dir / "no_vocals.wav", job_dir / BGM_UNTOUCHED
    if not raw_wav.exists():
        await asyncio.to_thread(shutil.copy2, bgm_wav, raw_wav)
    await apply_audio_effects(input_path=str(raw_wav), output_path=str(bgm_wav), volume_db=volume_db)
    await _create_preview(bgm_wav, job_dir / "no_vocals.preview.wav")
    await _create_playback_m4a(bgm_wav, job_dir / "no_vocals.m4a")


async def _write_fallback_stems(
    audio_path: Path,
    output_dir: Path,
    vocals_target: Path,
    bgm_target: Path,
) -> Tuple[str, str]:
    """
    Separation is unavailable — persist the original mixed audio as BOTH
    stems instead of only returning audio_path in memory.

    Previously this path wrote nothing to disk: no_vocals.wav never existed,
    so (a) the editor's Vocals/BGM waveform tracks were simply empty, and
    (b) the final mix only includes a BGM bed when no_vocals.wav is present
    — with mute_original=True (the default), that meant a separation
    failure silently produced a dubbed export with NO music or sound
    effects at all, dead silence behind every line, and no warning that it
    happened. A same-file copy isn't a real separation (dialogue still
    bleeds into the "BGM" bed) but it's far closer to correct than dropping
    the score entirely.
    """
    try:
        if not vocals_target.exists():
            await asyncio.to_thread(shutil.copy2, audio_path, vocals_target)
        if not bgm_target.exists():
            await asyncio.to_thread(shutil.copy2, audio_path, bgm_target)
    except OSError as e:
        logger.warning(f"Could not persist fallback stems to disk: {e}")
        return str(audio_path), str(audio_path)

    await _create_preview(vocals_target, output_dir / "vocals.preview.wav")
    await _create_preview(bgm_target,    output_dir / "no_vocals.preview.wav")
    await _create_playback_m4a(vocals_target, output_dir / "vocals.m4a")
    await _create_playback_m4a(bgm_target,    output_dir / "no_vocals.m4a")
    return str(vocals_target), str(bgm_target)


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
    # /process_uploaded_file → (vocals_filepath, accompaniment_filepath, info_html)
    result = client.predict(
        handle_file(audio_path),
        api_name="/process_uploaded_file",
    )
    return result[0], result[1]


async def _to_wav(src: str, dest: Path) -> bool:
    """Transcode any audio file to 44.1kHz stereo 16-bit WAV (the Space may return MP3)."""
    cmd = [
        ffmpeg(), "-y", "-i", str(src),
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


async def _to_flac(src: Path, dest: Path) -> bool:
    """Lossless and about half the size of the WAV — a faster upload to the Space."""
    proc = await asyncio.create_subprocess_exec(
        ffmpeg(), "-y", "-i", str(src), "-c:a", "flac", str(dest),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    return proc.returncode == 0


async def _separate_via_hf(
    audio_path: Path,
    output_dir: Path,
    vocals_target: Path,
    bgm_target: Path,
    timeout: float = 300.0,
) -> Tuple[Tuple[str, str] | None, str]:
    """
    Separate via the HF Space — compute happens in the cloud.
    Returns ((vocals, bgm), "") or (None, why it failed).
    """
    if not settings.SEPARATION_HF_SPACE:
        return None, "No separation service is set up."
    logger.info(f"Separating via HF Space '{settings.SEPARATION_HF_SPACE}': {audio_path.name}")
    upload = output_dir / "separation-upload.flac"
    source = upload if await _to_flac(audio_path, upload) else audio_path
    try:
        vocals_src, bgm_src = await asyncio.wait_for(
            asyncio.to_thread(_hf_separate_call, str(source)),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(f"HF separation timed out after {timeout:.0f}s — Space is unresponsive")
        return None, f"The separation service didn't finish within {timeout / 60:.0f} minutes."
    except Exception as e:
        logger.warning(f"HF separation failed: {e}")
        return None, f"The separation service failed: {str(e)[:200]}"
    finally:
        upload.unlink(missing_ok=True)

    # The Space returns MP3 — transcode both stems to real WAV for downstream tools
    if not await _to_wav(vocals_src, vocals_target) or not await _to_wav(bgm_src, bgm_target):
        return None, "The separation service returned audio that couldn't be read."

    await _create_preview(vocals_target, output_dir / "vocals.preview.wav")
    await _create_preview(bgm_target,    output_dir / "no_vocals.preview.wav")
    await _create_playback_m4a(vocals_target, output_dir / "vocals.m4a")
    await _create_playback_m4a(bgm_target,    output_dir / "no_vocals.m4a")
    logger.info(f"HF separation done: {vocals_target.name} + {bgm_target.name}")
    return (str(vocals_target), str(bgm_target)), ""


async def _create_playback_m4a(source: Path, dest: Path) -> None:
    """
    Encode a stem to AAC for in-editor playback. The editor's <audio> tags
    stream these instead of the raw WAVs — same audibility at ~7% of the bytes.
    The WAV stems remain the source of truth for diarization and the final mix.
    """
    cmd = [
        ffmpeg(), "-y",
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
        ffmpeg(), "-y",
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
