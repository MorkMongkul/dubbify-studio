"""
app/services/audio_extractor.py
Extracts audio and subtitles from a video file using ffmpeg.
"""
import asyncio
import json
import os
import sys
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
        "-acodec", "pcm_s16le",        # 16-bit PCM
        "-ar", "44100",                # 44.1kHz — separation models (Demucs/RoFormer)
                                       # are trained on this; 16kHz gave muddy stems.
        "-ac", "2",                    # stereo — models use stereo cues to separate.
        str(audio_path),               # diarization/Whisper run in the cloud and
                                       # resample internally, so they don't need 16k.
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
    bgm_path: str | None = None,
    video_layers: list | None = None,
    video_width: int | None = None,
    video_height: int | None = None,
) -> str:
    """
    Mix TTS audio segments back onto the video timeline using ffmpeg, and
    optionally composite video layers (dropped images, burned-in subtitle
    lines, plain color/blur boxes) on top of the video.

    Args:
        video_path:    Original video file
        tts_segments:  List of dicts: {start_time, audio_path, duration,
                       fit_rate?}. `fit_rate` > 1 speeds the clip up via
                       atempo so it fits its segment window — it must match
                       the editor's auto-fit playback rate, or the export
                       sounds slower than the preview.
        output_path:   Where to save the dubbed video
        mute_original: Whether to silence the original audio track
        bgm_path:      Optional path to a separated BGM/no_vocals WAV file.
                       When provided, uses clean BGM instead of the original
                       mixed audio, eliminating any original voice bleed-through.
        video_layers:  Optional list of positioned layers, composited in list
                       order — **the caller must pass them in real z-index
                       order**, since e.g. a "shape" meant to cover something
                       and a subtitle meant to read on top of it only work
                       correctly if they're interleaved in the right order,
                       not grouped by kind. Each entry has a `kind`:
                         - kind="image": {media_path, x, y, width, height,
                           opacity, start_time, end_time} — a positioned
                           image (dropped logo, or one pre-rendered subtitle
                           line PNG).
                         - kind="shape": {x, y, width, height, color, opacity,
                           blur, start_time, end_time} — a plain box, no
                           media file. `blur=True` blurs the video underneath
                           instead of filling it with `color`; typically used
                           to cover something already burned into the source
                           video (e.g. an original-language subtitle) so a
                           new subtitle overlay reads cleanly on top of it.
                       x/y/width/height are fractions (0-1) of the video's
                       own dimensions; start_time/end_time may be None for
                       "visible the whole video".

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

    # Optional: inject separated BGM as a second input
    bgm_input_idx: int | None = None
    if bgm_path and Path(bgm_path).exists():
        inputs += ["-i", bgm_path]
        bgm_input_idx = 1  # input index 1 = BGM file

    tts_offset = 2 if bgm_input_idx is not None else 1

    # Each clip stream stays FINITE (adelay only — no per-stream apad): a clip
    # is processed for start_time + duration instead of the whole movie, so the
    # mixing cost scales with total speech, not segments × film length. The
    # single apad AFTER the mix (below) restores the infinite tail that
    # -shortest needs so the video is never truncated at the last dubbed line.
    for i, seg in enumerate(tts_segments):
        inputs += ["-i", seg["audio_path"]]
        delay_ms = int(seg["start_time"] * 1000)
        label = f"[a{i}]"
        # A single atempo filter caps at 2.0× — chain filters for higher rates.
        # adelay fed by atempo emits AV_NOPTS timestamps (ffmpeg 8.x), which
        # corrupts the mux (near-zero audio duration) — asetpts regenerates
        # sample-accurate pts after the delay silence is inserted.
        fit_rate = seg.get("fit_rate") or 1.0
        tempo, setpts = "", ""
        if fit_rate > 1.001:
            r = fit_rate
            steps = []
            while r > 2.0:
                steps.append("atempo=2.0")
                r /= 2.0
            steps.append(f"atempo={r:.4f}")
            tempo = ",".join(steps) + ","
            setpts = ",asetpts=N/SR/TB"
        filter_parts.append(
            f"[{tts_offset + i}:a]{tempo}adelay={delay_ms}|{delay_ms}{setpts}{label}"
        )
        mix_labels.append(label)

    n = len(tts_segments)

    # amix in groups: hundreds of inputs in a single amix make an enormous
    # filter node and can brush up against argv/filter-graph limits — submix
    # in chunks, then mix the submixes.
    AMIX_GROUP = 32
    if n <= AMIX_GROUP:
        filter_parts.append(f"{''.join(mix_labels)}amix=inputs={n}:normalize=0[dubmix]")
    else:
        group_labels = []
        for g in range(0, n, AMIX_GROUP):
            chunk = mix_labels[g:g + AMIX_GROUP]
            glabel = f"[g{g // AMIX_GROUP}]"
            filter_parts.append(f"{''.join(chunk)}amix=inputs={len(chunk)}:normalize=0{glabel}")
            group_labels.append(glabel)
        filter_parts.append(
            f"{''.join(group_labels)}amix=inputs={len(group_labels)}:normalize=0[dubmix]"
        )

    # Pad the combined dub track once so -shortest cuts at the VIDEO's end,
    # not at the last TTS clip's end.
    filter_parts.append("[dubmix]apad[dubbed]")

    # Determine final audio:
    #   bgm_path provided  → BGM (full volume) + dubbed TTS (full volume)
    #   mute_original=True → dubbed TTS only (no background)
    #   mute_original=False → original audio at 15% + dubbed TTS
    if bgm_input_idx is not None:
        filter_parts.append(f"[{bgm_input_idx}:a][dubbed]amix=inputs=2:normalize=0[final]")
        audio_map = "[final]"
    elif mute_original:
        audio_map = "[dubbed]"
    else:
        filter_parts.append(f"[0:a]volume=0.15[orig];[orig][dubbed]amix=inputs=2[final]")
        audio_map = "[final]"

    # ── Optional video layers (dropped images, burned-in subtitle lines,
    # plain color/blur boxes) — composited in the given list order, which
    # must already be true z-index order so e.g. a "cover the original
    # subtitle" shape and the new subtitle text interleave correctly rather
    # than one kind always landing above the other regardless of z-index.
    # Stream-copying the video (-c:v copy) is only possible when no video
    # filter is applied — adding any layer means decoding/re-encoding.
    next_input_idx = tts_offset + n
    video_map = "0:v"
    needs_video_encode = False
    video_chain_label = "0:v"

    if video_layers:
        if not video_width or not video_height:
            raise ValueError("video_width/video_height are required when video_layers is set")
        for i, layer in enumerate(video_layers):
            target_w = max(1, round(video_width * layer["width"]))
            target_h = max(1, round(video_height * layer["height"]))
            px = round(video_width * layer["x"])
            py = round(video_height * layer["y"])
            enable = ""
            start, end = layer.get("start_time"), layer.get("end_time")
            if start is not None and end is not None:
                enable = f":enable='between(t,{start},{end})'"

            out_label = f"v{i}"

            if layer["kind"] == "shape":
                if layer.get("blur"):
                    # Split the current frame in two: one copy stays
                    # untouched as the base, the other gets cropped to just
                    # this box, blurred, then overlaid back at the same
                    # position it was cropped from.
                    base_label = f"shbase{i}"
                    crop_label = f"shcrop{i}"
                    blurred_label = f"shblur{i}"
                    filter_parts.append(f"[{video_chain_label}]split=2[{base_label}][{crop_label}]")
                    filter_parts.append(f"[{crop_label}]crop={target_w}:{target_h}:{px}:{py},boxblur=20:5[{blurred_label}]")
                    filter_parts.append(f"[{base_label}][{blurred_label}]overlay=x={px}:y={py}{enable}[{out_label}]")
                else:
                    color = layer.get("color") or "black"
                    opacity = layer.get("opacity", 0.85)
                    filter_parts.append(
                        f"[{video_chain_label}]drawbox=x={px}:y={py}:w={target_w}:h={target_h}:"
                        f"color={color}@{opacity}:t=fill{enable}[{out_label}]"
                    )
            else:  # kind == "image"
                inputs += ["-i", layer["media_path"]]
                layer_idx = next_input_idx
                next_input_idx += 1
                opacity = layer.get("opacity", 1.0)

                scaled_label = f"ov{i}"
                filter_parts.append(
                    f"[{layer_idx}:v]scale={target_w}:{target_h},"
                    f"format=rgba,colorchannelmixer=aa={opacity}[{scaled_label}]"
                )
                filter_parts.append(
                    f"[{video_chain_label}][{scaled_label}]overlay=x={px}:y={py}{enable}[{out_label}]"
                )

            video_chain_label = out_label
        needs_video_encode = True

    if needs_video_encode:
        video_map = f"[{video_chain_label}]"

    filter_complex = ";".join(filter_parts)

    def _build_cmd(video_codec_args: list[str]) -> list[str]:
        return [
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", video_map,       # original or overlaid video track
            "-map", audio_map,       # dubbed audio
            *video_codec_args,
            "-c:a", "aac",           # encode audio as AAC
            "-b:a", "192k",
            "-shortest",
            output_path,
        ]

    async def _run(cmd: list[str]) -> tuple[int, str]:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        return process.returncode, stderr.decode()

    logger.info(f"Mixing {n} dubbed segments into final video...")

    if not needs_video_encode:
        rc, err = await _run(_build_cmd(["-c:v", "copy"]))
    else:
        # Prefer the hardware encoder on macOS (several times faster than
        # software x264 for a full film); fall back to libx264 if this ffmpeg
        # build lacks it or the hardware session fails.
        rc, err = -1, ""
        if sys.platform == "darwin":
            rc, err = await _run(_build_cmd(["-c:v", "h264_videotoolbox", "-b:v", "6000k"]))
            if rc != 0:
                logger.warning(f"h264_videotoolbox failed (falling back to libx264): {err[-300:]}")
        if rc != 0:
            rc, err = await _run(_build_cmd(["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]))

    if rc != 0:
        raise RuntimeError(f"Audio mixing failed: {err}")

    logger.info(f"Dubbed video saved: {output_path}")
    return output_path


async def apply_audio_effects(
    input_path: str,
    output_path: str,
    volume_db: float = 0.0,
    voice_filter: str = "",
    voice_speed: float = 1.0,
) -> float:
    """
    Applies volume, speed, and voice filters to a WAV file using ffmpeg.
    Returns the new duration of the audio in seconds.
    """
    filters = []

    # 1. Volume filter
    if volume_db != 0.0:
        filters.append(f"volume={volume_db}dB")

    # 2. Voice Filter presets
    if voice_filter == "echo":
        filters.append("aecho=0.8:0.88:60:0.4")
    elif voice_filter == "synth":
        filters.append("aphaser=type=t:decay=0.6")
    elif voice_filter == "bass":
        filters.append("bass=g=8:f=100")
    elif voice_filter == "phone":
        filters.append("highpass=f=200,lowpass=f=3000")

    # 3. Speed (atempo) filter
    if voice_speed != 1.0:
        # Clamp speed between 0.5 and 2.0 (ffmpeg atempo limits)
        speed = max(0.5, min(2.0, voice_speed))
        filters.append(f"atempo={speed}")

    if not filters:
        # Just copy file if no effects are selected (off the event loop)
        import shutil
        await asyncio.to_thread(shutil.copy2, input_path, output_path)
    else:
        filter_str = ",".join(filters)
        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-filter_complex", f"[0:a]{filter_str}[out]",
            "-map", "[out]",
            output_path
        ]
        logger.info(f"Applying filters to segment {Path(input_path).name}: {filter_str}")
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            logger.error(f"Failed to apply audio effects: {stderr.decode()}")
            # fallback to copy
            import shutil
            shutil.copy2(input_path, output_path)

    # Get new duration of the audio clip
    return await get_video_duration(output_path)
