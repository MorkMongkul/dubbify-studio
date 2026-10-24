"""
app/services/media_assets.py
Preparing a media upload (the Media tab, 0.4.0): what it is, a thumbnail, the
copy the editor previews, and its sound.

The editor can't show a transparent video's transparency from the file itself:
Safari / WKWebView don't decode ProRes alpha, and a <video> on screen draws
partly transparent areas as opaque (measured 2026-10-24 on the owner's sponsor
card: its 72% white panel came out solid). So a transparent video's preview is
an ordinary H.264 file whose frames hold the colour on top and the alpha matte
below; the editor recombines them on a canvas (frontend MediaCanvas). H.264
decodes on every Mac the app runs on. The export reads the original.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from sqlalchemy import select

from app.core.binaries import ffmpeg
from app.core.paths import resolve_media_path
from app.models.models import MediaAsset
from app.services.audio_extractor import _has_encoder, _run_ffmpeg, probe_video

logger = logging.getLogger(__name__)

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".gif"}
# Previews are at most this many pixels on their long side (per half when stacked)
PREVIEW_MAX = 1280
THUMB_MAX = 320
# Pixel formats with an alpha plane (GIF transparency decodes as bgra; paletted PNGs as pal8)
_ALPHA_FORMATS = {"rgba", "bgra", "argb", "abgr", "pal8", "rgba64be", "rgba64le", "bgra64be", "bgra64le"}


@dataclass
class AssetFacts:
    kind: str                 # "image" | "video"
    width: int
    height: int
    duration_secs: float | None
    has_alpha: bool
    alpha_kept: bool          # false where the bundled ffmpeg can't read the transparency
    has_audio: bool
    animated_webp: bool = False


class UnknownLength(ValueError):
    """A video whose file doesn't say how long it is (as some browser recordings)."""


def _has_alpha_plane(pix_fmt: str) -> bool:
    return pix_fmt.startswith(("yuva", "gbrap", "ya")) or pix_fmt in _ALPHA_FORMATS


def _animated_webp(path: Path) -> bool:
    try:
        with Image.open(path) as im:
            return bool(getattr(im, "is_animated", False))
    except Exception:
        return False


def _still_gif(path: Path) -> bool:
    """A GIF of one frame: a picture, not a video (it has no length)."""
    try:
        with Image.open(path) as im:
            return getattr(im, "n_frames", 1) <= 1
    except Exception:
        return False


async def probe_asset(path: Path) -> AssetFacts:
    """What an upload is. Raises ValueError when it's neither an image nor a video."""
    ext = path.suffix.lower()
    if ext not in IMAGE_EXTS | VIDEO_EXTS:
        raise ValueError("not an image or a video")
    if ext == ".webp" and _animated_webp(path):
        # The bundled ffmpeg can't read animated WebP at all
        return AssetFacts("video", 0, 0, None, False, False, False, animated_webp=True)
    info = await probe_video(str(path))
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ValueError("not an image or a video")
    kind = "image" if ext in IMAGE_EXTS or (ext == ".gif" and _still_gif(path)) else "video"
    tags = {k.lower(): v for k, v in (video.get("tags") or {}).items()}
    webm_alpha = str(tags.get("alpha_mode", "")) == "1"
    has_alpha = _has_alpha_plane(video.get("pix_fmt") or "") or webm_alpha
    duration = None
    if kind == "video":
        for raw in ((info.get("format") or {}).get("duration"), video.get("duration")):
            try:
                duration = float(raw)
            except (TypeError, ValueError):
                continue
            if duration > 0:
                break
        if not duration or duration <= 0:
            # As a clip it would be clamped to a sliver that can't be stretched
            raise UnknownLength("This video doesn't say how long it is")
    return AssetFacts(
        kind=kind,
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        duration_secs=duration,
        has_alpha=has_alpha,
        # WebM's alpha needs libvpx and HEVC's its alpha layer: neither is in the bundled ffmpeg
        alpha_kept=has_alpha and not webm_alpha and video.get("codec_name") != "hevc",
        has_audio=kind == "video" and any(s.get("codec_type") == "audio" for s in streams),
    )


async def _encode_h264(args_before: list[str], out: Path, args_after: list[str]) -> None:
    """H.264 with the Mac's encoder, else libx264 where this ffmpeg has it (the bundled one doesn't)."""
    attempts = []
    if sys.platform == "darwin":
        attempts.append(["-c:v", "h264_videotoolbox", "-b:v", "4000k"])
    if sys.platform != "darwin" or await _has_encoder("libx264"):
        attempts.append(["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"])
    err = ""
    for codec in attempts:
        rc, err = await _run_ffmpeg([ffmpeg(), "-y", *args_before, *codec, *args_after, str(out)])
        if rc == 0:
            return
    raise RuntimeError(f"Making the preview failed: {err[-500:]}")


async def make_preview(src: Path, out_dir: Path, facts: AssetFacts) -> dict:
    """The thumbnail, the preview copy ('' for an image: the original is shown) and the sound (None without)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    thumb = out_dir / "thumb.png"
    seek = ["-ss", f"{(facts.duration_secs or 0) / 2:.3f}"] if facts.kind == "video" else []
    rc, err = await _run_ffmpeg([ffmpeg(), "-y", *seek, "-i", str(src), "-frames:v", "1", "-vf",
                                 f"scale=w='min(iw,{THUMB_MAX})':h='min(ih,{THUMB_MAX})':force_original_aspect_ratio=decrease",
                                 str(thumb)])
    if rc != 0:
        raise RuntimeError(f"Making the thumbnail failed: {err[-500:]}")
    if facts.kind == "image":
        return {"thumb": str(thumb), "preview": "", "audio": None}

    preview = out_dir / "preview.mp4"
    # Only ever smaller, never enlarged; even sizes for H.264
    fit = (f"scale=w='min(iw,{PREVIEW_MAX})':h='min(ih,{PREVIEW_MAX})':force_original_aspect_ratio=decrease,"
           "scale=trunc(iw/2)*2:trunc(ih/2)*2")
    if facts.alpha_kept:
        # The colour on top, the alpha matte below — recombined by the editor's canvas
        graph = (f"[0:v]{fit},format=rgba,split[c][a];[a]alphaextract,format=yuv420p[m];"
                 "[c]format=yuv420p[c2];[c2][m]vstack[out]")
        await _encode_h264(["-i", str(src), "-filter_complex", graph, "-map", "[out]"], preview,
                           ["-an", "-movflags", "+faststart"])
    else:
        await _encode_h264(["-i", str(src), "-vf", f"{fit},format=yuv420p"], preview, ["-an", "-movflags", "+faststart"])

    audio = None
    if facts.has_audio:
        audio = out_dir / "audio.m4a"
        # A mono sound stays mono: the preview plays it on both sides at full level, as the export
        # does (pan=stereo|c0=c0|c1=c0); ffmpeg's -ac 2 would put it 3 dB under
        sound = next((s for s in (await probe_video(str(src))).get("streams") or [] if s.get("codec_type") == "audio"), {})
        channels = "1" if int(sound.get("channels") or 2) == 1 else "2"
        rc, err = await _run_ffmpeg([ffmpeg(), "-y", "-i", str(src), "-vn", "-ac", channels, "-c:a", "aac", "-b:a", "128k", str(audio)])
        if rc != 0:
            raise RuntimeError(f"Taking out the sound failed: {err[-500:]}")
    return {"thumb": str(thumb), "preview": str(preview), "audio": str(audio) if audio else None}


async def prepare_asset(asset_id: str, sessionmaker) -> None:
    """Make an uploaded asset's files in the background; it's 'ready' (or 'failed', with why) after."""
    async with sessionmaker() as s:
        asset = await s.get(MediaAsset, asset_id)
        if asset is None:
            return
        src = Path(resolve_media_path(asset.path))
        facts = AssetFacts(asset.kind, asset.width or 0, asset.height or 0, asset.duration_secs,
                           bool(asset.has_alpha), bool(asset.alpha_kept), bool(asset.has_audio))
    # No session open while ffmpeg runs (SQLite has one writer)
    try:
        made = await make_preview(src, src.parent, facts)
        status, error = "ready", None
    except Exception as e:  # noqa: BLE001 — any failure is shown on the asset's tile
        logger.exception(f"Preparing media {asset_id} failed")
        made, status, error = {}, "failed", str(e)[:500]
    async with sessionmaker() as s:
        asset = await s.get(MediaAsset, asset_id)
        if asset is None:
            return
        if made:
            asset.thumb_path = made["thumb"]
            asset.preview_path = made["preview"]
            asset.audio_path = made["audio"]
        asset.status = status
        asset.error = error
        await s.commit()


async def resume_interrupted(sessionmaker) -> None:
    """Prepare again the media left preparing when the app quit (its tile would wait for ever)."""
    try:
        async with sessionmaker() as s:
            ids = (await s.execute(select(MediaAsset.id).where(MediaAsset.status == "preparing"))).scalars().all()
        for asset_id in ids:
            await prepare_asset(asset_id, sessionmaker)
    except Exception:  # noqa: BLE001 — not fatal: the next launch tries again
        logger.exception("Couldn't prepare the media left preparing")
