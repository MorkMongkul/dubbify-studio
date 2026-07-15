"""
app/services/source_separator.py

Splits audio into Vocals and Background (BGM + SFX) stems using Demucs.

Demucs --two-stems=vocals produces exactly two files:
  vocals.wav      — isolated human voice (used for diarization / ASR)
  no_vocals.wav   — everything else: music, drums, bass, SFX (BGM track)

Both demucs and demucs-mlx (Apple Silicon MLX accelerated) are tried.
Falls back to no-op (returns original audio path) when neither is available.

Install (already in requirements):
  pip install demucs          # CPU / CUDA
  pip install demucs-mlx      # Apple Silicon MLX (faster on M-series Macs)
"""
import asyncio
import logging
import shutil
import sys
from pathlib import Path
from typing import Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)

# Output path templates
# demucs (CPU):      <output_dir>/htdemucs/<track_name>/vocals.wav
# demucs-mlx (MLX): <output_dir>/<track_name>/vocals.wav
_DEMUCS_SUBDIRS = ["htdemucs", ""]   # try htdemucs subdir first, then root


async def separate_vocals_bgm(audio_path: str, output_dir: str) -> Tuple[str, str]:
    """
    Separate audio into vocals.wav and no_vocals.wav in output_dir.

    Returns:
        (vocals_wav_path, bgm_wav_path)
        Returns (audio_path, audio_path) if no separator is available.
    """
    audio_path_obj = Path(audio_path)
    output_dir_obj = Path(output_dir)

    vocals_target = output_dir_obj / "vocals.wav"
    bgm_target    = output_dir_obj / "no_vocals.wav"

    # Skip if stems already exist (e.g. re-running a failed job)
    if vocals_target.exists() and bgm_target.exists():
        logger.info("Stem files already present — skipping separation")
        return str(vocals_target), str(bgm_target)

    # Cloud (HuggingFace) path — offloads compute off this machine. Tried first
    # when configured; falls back to local Demucs if it fails (rate limit/down).
    if (settings.SEPARATION_BACKEND or "local").lower() == "hf":
        result = await _separate_via_hf(audio_path_obj, output_dir_obj, vocals_target, bgm_target)
        if result:
            return result
        logger.warning("HF separation failed — falling back to local Demucs")

    # Use the same venv's executables so we don't need demucs on system PATH
    venv_bin = Path(sys.executable).parent
    demucs_mlx_bin = venv_bin / "demucs-mlx"
    demucs_bin     = venv_bin / "demucs"

    # 1. Try demucs-mlx (Apple Silicon MLX — fast GPU-accelerated)
    # Uses -n htdemucs_2stems model which natively produces vocals/no_vocals
    # Note: demucs-mlx does NOT support --two-stems flag (different CLI from cpu demucs)
    if demucs_mlx_bin.exists():
        result = await _run_demucs(
            executable=[str(demucs_mlx_bin), "-n", "htdemucs_2stems"],
            audio_path=audio_path_obj,
            output_dir=output_dir_obj,
            vocals_target=vocals_target,
            bgm_target=bgm_target,
            model_subdir="htdemucs_2stems",
        )
        if result:
            return result

    # 2. Try demucs via venv binary (CPU — universal fallback)
    # Uses --two-stems vocals which outputs to output_dir/htdemucs/{track}/
    if demucs_bin.exists():
        result = await _run_demucs(
            executable=[str(demucs_bin), "--two-stems", "vocals"],
            audio_path=audio_path_obj,
            output_dir=output_dir_obj,
            vocals_target=vocals_target,
            bgm_target=bgm_target,
            model_subdir="htdemucs",
        )
        if result:
            return result

    # 3. Module invocation fallback (python -m demucs)
    result = await _run_demucs(
        executable=[sys.executable, "-m", "demucs", "--two-stems", "vocals"],
        audio_path=audio_path_obj,
        output_dir=output_dir_obj,
        vocals_target=vocals_target,
        bgm_target=bgm_target,
        model_subdir="htdemucs",
    )
    if result:
        return result

    logger.warning("No source separator succeeded — pipeline will use original mixed audio")
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
    logger.info(f"HF separation done: {vocals_target.name} + {bgm_target.name}")
    return str(vocals_target), str(bgm_target)


async def _run_demucs(
    executable: list,
    audio_path: Path,
    output_dir: Path,
    vocals_target: Path,
    bgm_target: Path,
    model_subdir: str | None,
) -> Tuple[str, str] | None:
    """
    Run a demucs or demucs-mlx command.
    The caller builds the full executable+flags list.

    model_subdir: name of the model subfolder demucs creates inside output_dir,
                  e.g. "htdemucs" for cpu demucs, "htdemucs_2stems" for mlx.
                  None means output goes directly to output_dir/{track_name}/.

    Returns (vocals_path, bgm_path) on success, None on failure.
    """
    cmd = [
        *executable,
        "-o", str(output_dir),
        str(audio_path),
    ]

    logger.info(f"Running: {' '.join(str(x) for x in cmd[:4])} ... {audio_path.name}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            logger.warning(
                f"{executable[0]} exited {proc.returncode}: {stderr.decode()[:300]}"
            )
            return None

        # Locate output files
        track_name = audio_path.stem
        if model_subdir:
            stem_dir = output_dir / model_subdir / track_name
        else:
            stem_dir = output_dir / track_name

        vocals_out   = stem_dir / "vocals.wav"
        no_vocals_out = stem_dir / "no_vocals.wav"

        if not vocals_out.exists():
            logger.warning(f"Expected vocals at {vocals_out} — not found")
            return None

        shutil.copy(vocals_out, vocals_target)

        if no_vocals_out.exists():
            shutil.copy(no_vocals_out, bgm_target)
        else:
            logger.warning(f"no_vocals.wav not found at {no_vocals_out} — BGM will be silent")
            shutil.copy(vocals_target, bgm_target)

        # Create tiny 8kHz mono preview WAVs for fast waveform visualization.
        # Full stems are ~19MB; previews are ~1.7MB — Web Audio API decodes instantly.
        await _create_preview(vocals_target, output_dir / "vocals.preview.wav")
        await _create_preview(bgm_target,    output_dir / "no_vocals.preview.wav")

        logger.info(
            f"Separation done: {vocals_target.name} + {bgm_target.name} (+ .preview.wav files)"
        )
        return str(vocals_target), str(bgm_target)

    except Exception:
        logger.exception(f"Demucs subprocess failed")
        return None


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
