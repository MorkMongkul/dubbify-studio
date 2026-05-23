"""
app/services/audio_extractor.py
Extracts audio and subtitles from a video file using ffmpeg.
"""
import asyncio
import json
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


async def probe_video(video_path: str) -> dict:
    """
    Run ffprobe to get full stream info from a video file.
    Returns parsed JSON with all streams (video, audio, subtitle).
    """
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        str(video_path),
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await process.communicate()
    if process.returncode != 0:
        return {}
    try:
        return json.loads(stdout.decode())
    except json.JSONDecodeError:
        return {}


async def list_subtitle_tracks(video_path: str) -> list:
    """
    List all subtitle tracks embedded in a video file.

    Returns list of dicts:
    [
      {"index": 2, "codec": "subrip", "language": "chi", "title": "Chinese"},
      {"index": 3, "codec": "ass",    "language": "eng", "title": "English"},
    ]
    """
    probe = await probe_video(video_path)
    streams = probe.get("streams", [])

    subtitles = []
    for stream in streams:
        if stream.get("codec_type") != "subtitle":
            continue

        tags    = stream.get("tags", {})
        lang    = tags.get("language", "und").lower()
        title   = tags.get("title", "")
        codec   = stream.get("codec_name", "unknown")
        index   = stream.get("index", 0)

        subtitles.append({
            "index":    index,
            "codec":    codec,
            "language": lang,
            "title":    title,
        })

    logger.info(f"Found {len(subtitles)} subtitle track(s) in {Path(video_path).name}")
    return subtitles


async def extract_subtitle(
    video_path: str,
    output_dir: str,
    track_index: int = None,
    prefer_language: str = "chi",
) -> str | None:
    """
    Extract a subtitle track from a video file using ffmpeg.

    Auto-selects the best track if track_index is not specified:
      1. Prefers the language matching prefer_language (default: "chi" for Chinese)
      2. Falls back to first available subtitle track

    Returns path to extracted .srt file, or None if no subtitles found.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Get available subtitle tracks
    tracks = await list_subtitle_tracks(video_path)
    if not tracks:
        logger.info(f"No embedded subtitles found in {Path(video_path).name}")
        return None

    # Pick the right track
    if track_index is not None:
        # Use specified track
        selected = next((t for t in tracks if t["index"] == track_index), tracks[0])
    else:
        # Auto-select: prefer Chinese, fall back to first track
        chinese_lang_codes = {"chi", "zho", "zh", "cmn"}
        selected = next(
            (t for t in tracks if t["language"] in chinese_lang_codes),
            tracks[0]  # fallback to first track
        )

    logger.info(
        f"Extracting subtitle track {selected['index']} "
        f"(lang={selected['language']}, codec={selected['codec']})"
    )

    output_path = output_dir / "subtitle.srt"

    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(video_path),
        "-map", f"0:{selected['index']}",   # select specific subtitle stream
        "-c:s", "srt",                       # convert to SRT format
        str(output_path),
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        error = stderr.decode()
        logger.warning(f"Subtitle extraction failed: {error[:200]}")
        return None

    if not output_path.exists() or output_path.stat().st_size == 0:
        logger.warning("Subtitle extraction produced empty file")
        return None

    logger.info(f"Subtitle extracted: {output_path} ({output_path.stat().st_size} bytes)")
    return str(output_path)


async def extract_audio(video_path: str, output_dir: str) -> str:
    """
    Extract audio from video file and save as 16kHz mono WAV.

    Args:
        video_path: Path to input video (.mp4, .mkv, etc.)
        output_dir: Directory to save extracted audio

    Returns:
        Path to extracted .wav file

    Raises:
        RuntimeError: if ffmpeg fails
    """
    video_path = Path(video_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    audio_path = output_dir / f"{video_path.stem}_audio.wav"

    cmd = [
        "ffmpeg",
        "-y",                          # overwrite output if exists
        "-i", str(video_path),         # input file
        "-vn",                         # no video
        "-acodec", "pcm_s16le",        # 16-bit PCM — Whisper-compatible
        "-ar", "16000",                # 16kHz sample rate
        "-ac", "1",                    # mono channel
        str(audio_path),
    ]

    logger.info(f"Extracting audio: {video_path.name} → {audio_path.name}")

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        error = stderr.decode()
        logger.error(f"ffmpeg failed: {error}")
        raise RuntimeError(f"Audio extraction failed: {error}")

    logger.info(f"Audio extracted successfully: {audio_path}")
    return str(audio_path)


async def get_video_duration(video_path: str) -> float:
    """
    Get video duration in seconds using ffprobe.

    Returns:
        Duration in seconds, or 0.0 on failure
    """
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(video_path),
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        return 0.0

    import json
    try:
        data = json.loads(stdout.decode())
        return float(data["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError):
        return 0.0


async def mix_dubbed_audio(
    video_path: str,
    tts_segments: list,
    output_path: str,
    mute_original: bool = True,
) -> str:
    """
    Mix TTS audio segments back onto the video timeline using ffmpeg.

    Args:
        video_path:    Original video file
        tts_segments:  List of dicts: {start_time, audio_path, duration}
        output_path:   Where to save the dubbed video
        mute_original: Whether to silence the original audio track

    Returns:
        Path to output dubbed video file
    """
    if not tts_segments:
        raise ValueError("No TTS segments provided for mixing")

    # Build a complex ffmpeg filter to place each TTS segment at the right timestamp
    # Each segment is delayed by its start_time and then all are mixed together
    inputs = ["-i", video_path]
    filter_parts = []
    mix_labels = []

    for i, seg in enumerate(tts_segments):
        inputs += ["-i", seg["audio_path"]]
        delay_ms = int(seg["start_time"] * 1000)
        label = f"[a{i}]"
        filter_parts.append(
            f"[{i+1}:a]adelay={delay_ms}|{delay_ms},apad{label}"
        )
        mix_labels.append(label)

    # Mix all delayed TTS streams together
    mix_inputs = "".join(mix_labels)
    n = len(tts_segments)
    filter_parts.append(f"{mix_inputs}amix=inputs={n}:normalize=0[dubbed]")

    # If muting original: just use dubbed track
    # If keeping original: mix with volume balance
    if mute_original:
        audio_map = "[dubbed]"
        filter_complex = ";".join(filter_parts)
    else:
        filter_parts.append(f"[0:a]volume=0.15[orig];[orig][dubbed]amix=inputs=2[final]")
        audio_map = "[final]"
        filter_complex = ";".join(filter_parts)

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "0:v",           # original video track
        "-map", audio_map,       # dubbed audio
        "-c:v", "copy",          # don't re-encode video
        "-c:a", "aac",           # encode audio as AAC
        "-b:a", "192k",
        "-shortest",
        output_path,
    ]

    logger.info(f"Mixing {n} dubbed segments into final video...")

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        raise RuntimeError(f"Audio mixing failed: {stderr.decode()}")

    logger.info(f"Dubbed video saved: {output_path}")
    return output_path
