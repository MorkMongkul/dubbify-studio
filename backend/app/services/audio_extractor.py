"""
app/services/audio_extractor.py
Extracts audio and subtitles from a video file using ffmpeg.
"""
import asyncio
import json
import os
import re
import shutil
import sys
import logging
from app.core.binaries import ffmpeg, ffprobe
from pathlib import Path

logger = logging.getLogger(__name__)


async def probe_video(video_path: str) -> dict:
    """
    Run ffprobe to get full stream info from a video file.
    Returns parsed JSON with all streams (video, audio, subtitle).
    """
    cmd = [
        ffprobe(),
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
        ffmpeg(),
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
        ffmpeg(),
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


def frame_rate_of(probe: dict) -> float | None:
    """The video stream's frame rate from ffprobe's JSON (one ← / → step in the editor), or None."""
    video = next((st for st in probe.get("streams", []) if st.get("codec_type") == "video"), None)
    if not video:
        return None
    for key in ("r_frame_rate", "avg_frame_rate"):
        try:
            num, _, den = str(video.get(key) or "").partition("/")
            rate = float(num) / float(den or 1)
        except (ValueError, ZeroDivisionError):
            continue
        if 1 <= rate <= 240:
            return round(rate, 3)
    return None


async def video_frame_rate(video_path: str) -> float | None:
    """The video's frame rate, or None when it can't be read."""
    try:
        return frame_rate_of(await probe_video(video_path))
    except Exception:  # noqa: BLE001 — a missing frame rate just means 1/30 s steps
        return None


async def get_video_duration(video_path: str) -> float:
    """
    Get video duration in seconds using ffprobe.

    Returns:
        Duration in seconds, or 0.0 on failure
    """
    cmd = [
        ffprobe(),
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


# Voices are mixed into the dub track in batches of this many clips per ffmpeg
# run. ffmpeg opens one file per input, and an app opened from Finder may only
# have 256 files open at once; a full episode has hundreds of lines.
DUB_BATCH = 48


async def _run_ffmpeg(cmd: list[str], *, duration: float | None = None, on_progress=None) -> tuple[int, str]:
    """
    Run ffmpeg; returns (returncode, stderr). With `duration` and
    `on_progress`, ffmpeg reports how far it got and on_progress(fraction)
    is awaited as the encode advances.
    """
    report = bool(duration and on_progress)
    if report:
        cmd = [*cmd[:1], "-progress", "pipe:1", "-nostats", *cmd[1:]]
    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    if not report:
        _, stderr = await process.communicate()
        return process.returncode, stderr.decode(errors="replace")

    stderr_task = asyncio.create_task(process.stderr.read())
    last = -1.0
    try:
        async for raw in process.stdout:
            key, _, value = raw.decode(errors="replace").strip().partition("=")
            if key == "out_time_us" and value.isdigit():
                fraction = min(1.0, int(value) / 1e6 / duration)
                if fraction - last >= 0.02:
                    last = fraction
                    try:
                        await on_progress(fraction)
                    except Exception as e:  # noqa: BLE001 — progress never stops an export
                        logger.warning(f"Progress update failed: {e}")
        await process.wait()
    finally:
        # Interrupted (e.g. the app quitting): don't leave ffmpeg writing on its own
        if process.returncode is None:
            process.kill()
            await process.wait()
    return process.returncode, (await stderr_task).decode(errors="replace")


_LOG_PREFIX = re.compile(r"^(?:\[[^\]]*\]\s*)+")
_ERROR_WORDS = re.compile(
    r"error|invalid|failed|cannot|could not|unable|unrecognized|not found|no such|denied|no space",
    re.IGNORECASE,
)


def ffmpeg_failure_reason(stderr: str) -> str:
    """
    The first line of ffmpeg's output that says what went wrong, without its
    "[component @ 0x…]" prefix. The lines after it are mostly consequences
    ("Task finished with error code…", "Conversion failed!"). Indented lines
    are stream details and metadata, never the error.
    """
    lines = [line for line in stderr.splitlines() if line.strip()]
    for line in lines:
        if line[0].isspace() or line.startswith(("Input #", "Output #")):
            continue
        text = _LOG_PREFIX.sub("", line).strip()
        if text and text != "Conversion failed!" and _ERROR_WORDS.search(text):
            return text[:300]
    return lines[-1].strip()[:300] if lines else "ffmpeg stopped without saying why"


async def _has_encoder(name: str) -> bool:
    """Whether this ffmpeg build has the encoder (the bundled LGPL one has no libx264)."""
    process = await asyncio.create_subprocess_exec(
        ffmpeg(), "-hide_banner", "-encoders",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await process.communicate()
    return any(line.split()[1:2] == [name] for line in stdout.decode(errors="replace").splitlines())


def _tempo_chain(fit_rate: float) -> str:
    """atempo filters for a speed-up (one atempo caps at 2.0×, so chain them)."""
    if fit_rate <= 1.001:
        return ""
    steps, r = [], fit_rate
    while r > 2.0:
        steps.append("atempo=2.0")
        r /= 2.0
    steps.append(f"atempo={r:.4f}")
    return ",".join(steps) + ","


async def _render_dub_part(clips: list, offset: float, output: Path) -> None:
    """
    Mix these clips into one mono float WAV that starts at `offset` seconds:
    each clip plays its window of its voice file — from "offset" for "length"
    seconds of the timeline at "fit_rate" (app/services/timeline.py, the same
    rule the editor's preview follows) — delayed to its start.
    """
    inputs, chains, labels = [], [], []
    for i, seg in enumerate(clips):
        inputs += ["-i", seg["audio_path"]]
        delay_ms = max(0, round((seg["start_time"] - offset) * 1000))
        rate = seg.get("fit_rate") or 1.0
        window = ""
        if "length" in seg:
            window = (f"atrim=start={seg.get('offset') or 0.0:.3f}:duration={seg['length'] * rate:.3f},"
                      f"asetpts=PTS-STARTPTS,")
        # adelay fed by atempo emits AV_NOPTS timestamps (ffmpeg 8.x), which
        # corrupts the mux — asetpts regenerates sample-accurate pts.
        tempo = _tempo_chain(rate)
        setpts = ",asetpts=N/SR/TB" if tempo else ""
        chains.append(
            f"[{i}:a]{window}{tempo}aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=mono,"
            f"adelay=delays={delay_ms}:all=1{setpts}[c{i}]"
        )
        labels.append(f"[c{i}]")
    chains.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=longest[out]")
    cmd = [ffmpeg(), "-y", *inputs, "-filter_complex", ";".join(chains),
           "-map", "[out]", "-c:a", "pcm_f32le", str(output)]
    rc, err = await _run_ffmpeg(cmd)
    if rc != 0:
        raise RuntimeError(f"Mixing the voices failed: {err[-1500:]}")


async def _sum_parts(parts: list[tuple[str, float]], output: Path) -> tuple[str, float]:
    """Mix adjacent dub parts into one that starts where the first one does."""
    offset = parts[0][1]
    inputs, chains, labels = [], [], []
    for i, (path, part_offset) in enumerate(parts):
        inputs += ["-i", path]
        delay_ms = max(0, round((part_offset - offset) * 1000))
        chains.append(f"[{i}:a]adelay=delays={delay_ms}:all=1[p{i}]")
        labels.append(f"[p{i}]")
    chains.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=longest[out]")
    cmd = [ffmpeg(), "-y", *inputs, "-filter_complex", ";".join(chains),
           "-map", "[out]", "-c:a", "pcm_f32le", str(output)]
    rc, err = await _run_ffmpeg(cmd)
    if rc != 0:
        raise RuntimeError(f"Mixing the voices failed: {err[-1500:]}")
    return str(output), offset


async def build_dub_parts(tts_segments: list, work_dir: Path, on_progress=None) -> list[tuple[str, float]]:
    """
    The dubbed voices as a few WAV parts, each covering a stretch of the
    timeline: (path, start offset in seconds). Clips are mixed DUB_BATCH at a
    time, and parts are combined until at most DUB_BATCH remain, so no ffmpeg
    run ever opens more than about DUB_BATCH files.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    clips = sorted(tts_segments, key=lambda s: s["start_time"])
    batches = [clips[i:i + DUB_BATCH] for i in range(0, len(clips), DUB_BATCH)]
    parts: list[tuple[str, float]] = []
    for k, batch in enumerate(batches):
        offset = batch[0]["start_time"]
        path = work_dir / f"dub_{k:04d}.wav"
        await _render_dub_part(batch, offset, path)
        parts.append((str(path), offset))
        if on_progress:
            await on_progress((k + 1) / len(batches))
    level = 0
    while len(parts) > DUB_BATCH:
        level += 1
        parts = [
            await _sum_parts(parts[g:g + DUB_BATCH], work_dir / f"dub_l{level}_{g // DUB_BATCH:04d}.wav")
            for g in range(0, len(parts), DUB_BATCH)
        ]
    return parts


async def mix_dubbed_audio(
    video_path: str,
    tts_segments: list,
    output_path: str,
    mute_original: bool = True,
    bgm_path: str | None = None,
    video_layers: list | None = None,
    video_width: int | None = None,
    video_height: int | None = None,
    on_progress=None,
) -> str:
    """
    Mix TTS audio segments back onto the video timeline using ffmpeg, and
    optionally composite video layers (dropped images, burned-in subtitles,
    plain color/blur boxes) on top of the video.

    The voices are first mixed into a few WAV parts (build_dub_parts), so the
    final ffmpeg run has a handful of inputs however many lines the episode
    has — one input per line used to exceed the open-file limit of an app
    opened from Finder (256) and fail on full episodes.

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
                           image (a dropped logo).
                         - kind="sequence": {concat_path, left_px, top_px} —
                           a timed image stream (every burned-in subtitle
                           line, video_overlay.build_subtitle_track), drawn
                           at its own pixel size.
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
        on_progress:   Optional async callback(fraction 0-1).

    Returns:
        Path to output dubbed video file
    """
    if not tts_segments:
        raise ValueError("No TTS segments provided for mixing")

    async def report(fraction: float) -> None:
        if on_progress:
            await on_progress(fraction)

    n = len(tts_segments)
    logger.info(f"Mixing {n} dubbed segments into final video...")
    work_dir = Path(output_path).parent / "mix_parts"
    try:
        return await _mix_with_parts(
            video_path, tts_segments, output_path, mute_original, bgm_path,
            video_layers, video_width, video_height, work_dir, report,
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


async def _mix_with_parts(
    video_path, tts_segments, output_path, mute_original, bgm_path,
    video_layers, video_width, video_height, work_dir: Path, report,
) -> str:
    parts = await build_dub_parts(
        tts_segments, work_dir, on_progress=lambda f: report(0.05 + 0.35 * f),
    )

    try:
        duration = await get_video_duration(video_path)
    except Exception:  # noqa: BLE001 — only progress and padding use it
        duration = 0.0

    inputs = ["-i", video_path]
    filter_parts = []

    # Optional: inject separated BGM as a second input
    bgm_input_idx: int | None = None
    if bgm_path and Path(bgm_path).exists():
        inputs += ["-i", bgm_path]
        bgm_input_idx = 1  # input index 1 = BGM file

    part_offset = 2 if bgm_input_idx is not None else 1
    part_labels = []
    for i, (path, offset) in enumerate(parts):
        inputs += ["-i", path]
        delay_ms = round(offset * 1000)
        filter_parts.append(f"[{part_offset + i}:a]adelay=delays={delay_ms}:all=1[d{i}]")
        part_labels.append(f"[d{i}]")
    filter_parts.append(
        f"{''.join(part_labels)}amix=inputs={len(part_labels)}:normalize=0:duration=longest[dubmix]"
    )

    # Pad the combined dub track to the video's length so -shortest cuts at
    # the VIDEO's end, not at the last TTS clip's end. Not endlessly: an
    # endless apad keeps generating silence once the video has ended, and
    # ffmpeg 8 sometimes queues it faster than -shortest stops it — the queue
    # overflows and the export fails with "No space left on device".
    pad = f"apad=whole_dur={duration:.6f}" if duration > 0 else "apad"
    # The voices are mono; made stereo here at full level on both sides, as the
    # preview plays them. Left to amix, ffmpeg's mono-to-stereo pan law put
    # them 3 dB lower against the (stereo) music than the user heard.
    filter_parts.append(f"[dubmix]{pad},pan=stereo|c0=c0|c1=c0[dubbed]")

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

    # ── Optional video layers (dropped images, burned-in subtitles, plain
    # color/blur boxes) — composited in the given list order, which must
    # already be true z-index order so e.g. a "cover the original subtitle"
    # shape and the new subtitle text interleave correctly rather than one
    # kind always landing above the other regardless of z-index.
    # Stream-copying the video (-c:v copy) is only possible when no video
    # filter is applied — adding any layer means decoding/re-encoding.
    next_input_idx = part_offset + len(parts)
    video_map = "0:v"
    needs_video_encode = False
    video_chain_label = "0:v"

    if video_layers:
        if not video_width or not video_height:
            raise ValueError("video_width/video_height are required when video_layers is set")
        for i, layer in enumerate(video_layers):
            out_label = f"v{i}"

            if layer["kind"] == "sequence":
                inputs += ["-f", "concat", "-safe", "0", "-i", layer["concat_path"]]
                seq_label = f"seq{i}"
                filter_parts.append(f"[{next_input_idx}:v]format=rgba[{seq_label}]")
                filter_parts.append(
                    f"[{video_chain_label}][{seq_label}]overlay=x={layer['left_px']}:y={layer['top_px']}"
                    f":eof_action=pass[{out_label}]"
                )
                next_input_idx += 1
                video_chain_label = out_label
                continue

            target_w = max(1, round(video_width * layer["width"]))
            target_h = max(1, round(video_height * layer["height"]))
            px = round(video_width * layer["x"])
            py = round(video_height * layer["y"])
            enable = ""
            start, end = layer.get("start_time"), layer.get("end_time")
            if start is not None and end is not None:
                enable = f":enable='between(t,{start},{end})'"

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
                    # gblur, not boxblur: boxblur is GPL-only and missing from the
                    # LGPL ffmpeg the desktop app ships. sigma 25 ≈ boxblur=20:5.
                    filter_parts.append(f"[{crop_label}]crop={target_w}:{target_h}:{px}:{py},gblur=sigma=25[{blurred_label}]")
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
            ffmpeg(), "-y",
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
        return await _run_ffmpeg(cmd, duration=duration, on_progress=lambda f: report(0.4 + 0.6 * f))

    if not needs_video_encode:
        rc, err = await _run(_build_cmd(["-c:v", "copy"]))
    else:
        # Prefer the hardware encoder on macOS (several times faster than
        # software x264 for a full film). Fall back to libx264 only where this
        # ffmpeg has it: the LGPL build bundled with the desktop app doesn't,
        # and trying it only replaced the real error with "Unrecognized option".
        rc, err = -1, ""
        if sys.platform == "darwin":
            rc, err = await _run(_build_cmd(["-c:v", "h264_videotoolbox", "-b:v", "6000k"]))
            if rc != 0:
                logger.error(f"h264_videotoolbox failed:\n{err[-3000:]}")
        if rc != 0 and (sys.platform != "darwin" or await _has_encoder("libx264")):
            rc, err = await _run(_build_cmd(["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]))

    if rc != 0:
        logger.error(f"Export failed:\n{err[-3000:]}")
        raise RuntimeError(ffmpeg_failure_reason(err))

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
            ffmpeg(), "-y",
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
