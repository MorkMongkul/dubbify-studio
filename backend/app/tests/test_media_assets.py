"""Media (0.4.0): preparing an upload — what it is, a thumbnail, the preview copy, its sound."""
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from app.core.binaries import ffmpeg
from app.models.models import MediaAsset, Project
from app.services import media_assets


def _has_encoder(name: str) -> bool:
    out = subprocess.run([ffmpeg(), "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return any(line.split()[1:2] == [name] for line in out.splitlines())


def _ff(*args):
    subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture
def prores_alpha(tmp_path) -> Path:
    """White at alpha 0.72 (184/255), like the sponsor card's panel."""
    path = tmp_path / "card.mov"
    _ff("-f", "lavfi", "-i", "color=c=white:s=64x128:d=1:r=10",
        "-vf", "format=rgba,colorchannelmixer=aa=0.72", "-c:v", "prores_ks", "-profile:v", "4444",
        "-pix_fmt", "yuva444p10le", str(path))
    return path


def _pixel_gray(video: Path, x: int, y: int) -> int:
    out = subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", str(video), "-frames:v", "1",
                          "-vf", f"format=gray,crop=1:1:{x}:{y}", "-f", "rawvideo", "-"], capture_output=True, check=True)
    return out.stdout[0]


def _size(video: Path) -> tuple[int, int]:
    out = subprocess.run([ffmpeg(), "-hide_banner", "-i", str(video)], capture_output=True, text=True).stderr
    import re
    w, h = re.search(r", (\d{2,5})x(\d{2,5})", out).groups()
    return int(w), int(h)


@pytest.mark.asyncio
async def test_a_prores_4444_clip_keeps_its_transparency(prores_alpha):
    facts = await media_assets.probe_asset(prores_alpha)
    assert (facts.kind, facts.width, facts.height) == ("video", 64, 128)
    assert facts.has_alpha and facts.alpha_kept and not facts.has_audio
    assert facts.duration_secs == pytest.approx(1.0, abs=0.05)


@pytest.mark.asyncio
async def test_its_preview_stacks_the_alpha_matte_under_the_colour(prores_alpha, tmp_path):
    facts = await media_assets.probe_asset(prores_alpha)
    made = await media_assets.make_preview(prores_alpha, tmp_path / "out", facts)
    preview = Path(made["preview"])
    assert _size(preview) == (64, 256)                            # colour on top, matte below
    assert _pixel_gray(preview, 32, 64) >= 240                    # the colour: white
    assert abs(_pixel_gray(preview, 32, 192) - 184) <= 8          # the matte: alpha 184
    assert Path(made["thumb"]).exists() and made["audio"] is None


@pytest.mark.asyncio
async def test_a_png_with_transparency_is_an_image(tmp_path):
    path = tmp_path / "logo.png"
    Image.new("RGBA", (40, 20), (255, 0, 0, 128)).save(path)
    facts = await media_assets.probe_asset(path)
    assert (facts.kind, facts.width, facts.height, facts.has_alpha, facts.alpha_kept) == ("image", 40, 20, True, True)
    made = await media_assets.make_preview(path, tmp_path / "out", facts)
    assert made["preview"] == "" and Path(made["thumb"]).exists()


@pytest.mark.asyncio
async def test_an_opaque_video_with_sound_gets_its_sound_and_a_plain_preview(tmp_path):
    path = tmp_path / "ad.mkv"
    _ff("-f", "lavfi", "-i", "testsrc=s=96x64:d=1:r=10", "-f", "lavfi", "-i", "sine=f=1000:d=1",
        "-c:v", "libx264" if _has_encoder("libx264") else "mpeg4", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path))
    facts = await media_assets.probe_asset(path)
    assert facts.kind == "video" and facts.has_audio and not facts.has_alpha
    made = await media_assets.make_preview(path, tmp_path / "out", facts)
    assert _size(Path(made["preview"])) == (96, 64)                # not stacked
    assert Path(made["audio"]).exists()


@pytest.mark.asyncio
@pytest.mark.skipif(not _has_encoder("libvpx-vp9"), reason="this ffmpeg can't write VP9")
async def test_webm_transparency_is_flagged_as_not_kept(tmp_path):
    path = tmp_path / "card.webm"
    _ff("-f", "lavfi", "-i", "color=c=white:s=64x128:d=1:r=10", "-vf", "format=rgba,colorchannelmixer=aa=0.72",
        "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", str(path))
    facts = await media_assets.probe_asset(path)
    assert facts.has_alpha and not facts.alpha_kept


@pytest.mark.asyncio
async def test_an_animated_webp_is_recognised(tmp_path):
    path = tmp_path / "sticker.webp"
    frames = [Image.new("RGBA", (32, 32), (255, 0, 0, 255)), Image.new("RGBA", (32, 32), (0, 0, 255, 255))]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=100, loop=0)
    assert (await media_assets.probe_asset(path)).animated_webp
    still = tmp_path / "still.webp"
    frames[0].save(still)
    assert not (await media_assets.probe_asset(still)).animated_webp


@pytest.mark.asyncio
async def test_preparing_marks_the_asset_ready_with_its_files(prores_alpha, db_sessionmaker):
    facts = await media_assets.probe_asset(prores_alpha)
    async with db_sessionmaker() as s:
        project = Project(name="Drama")
        s.add(project)
        await s.flush()
        asset = MediaAsset(project_id=project.id, name="card.mov", kind=facts.kind, path=str(prores_alpha),
                           width=facts.width, height=facts.height, duration_secs=facts.duration_secs,
                           has_alpha=facts.has_alpha, alpha_kept=facts.alpha_kept, has_audio=facts.has_audio)
        s.add(asset)
        await s.commit()
        asset_id = asset.id
    await media_assets.prepare_asset(asset_id, db_sessionmaker)
    async with db_sessionmaker() as s:
        asset = await s.get(MediaAsset, asset_id)
        assert asset.status == "ready", asset.error
        assert Path(asset.preview_path).exists() and Path(asset.thumb_path).exists()


def _rms_db(audio: Path, channel: int) -> float:
    """One channel's RMS level in dBFS (astats)."""
    out = subprocess.run([ffmpeg(), "-hide_banner", "-i", str(audio), "-af",
                          f"pan=mono|c0=c{channel},astats=metadata=0:reset=0", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    import re
    return float(re.findall(r"RMS level dB: (-?[\d.]+)", out)[-1])


@pytest.mark.asyncio
async def test_a_mono_sound_is_previewed_as_loud_as_the_export_plays_it(tmp_path):
    # The export puts a mono sound on both sides at full level (pan=stereo|c0=c0|c1=c0); the
    # preview's sound file must not be 3 dB under it (ffmpeg's -ac 2 pan law)
    path = tmp_path / "jingle.mkv"
    _ff("-f", "lavfi", "-i", "testsrc=s=96x64:d=1:r=10", "-f", "lavfi", "-i", "sine=f=1000:d=1",
        "-c:v", "libx264" if _has_encoder("libx264") else "mpeg4", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "1",
        "-shortest", str(path))
    facts = await media_assets.probe_asset(path)
    made = await media_assets.make_preview(path, tmp_path / "out", facts)
    source = _rms_db(path, 0)
    assert abs(_rms_db(Path(made["audio"]), 0) - source) < 0.5


@pytest.mark.asyncio
async def test_a_one_frame_gif_is_an_image(tmp_path):
    # A still GIF has no length: as a "video" it became a 0.1 s clip that couldn't be stretched
    path = tmp_path / "logo.gif"
    Image.new("RGB", (40, 20), (255, 0, 0)).save(path)
    facts = await media_assets.probe_asset(path)
    assert (facts.kind, facts.duration_secs) == ("image", None)
    moving = tmp_path / "spark.gif"
    frames = [Image.new("RGB", (40, 20), (255, 0, 0)), Image.new("RGB", (40, 20), (0, 0, 255))]
    frames[0].save(moving, save_all=True, append_images=frames[1:], duration=100, loop=0)
    assert (await media_assets.probe_asset(moving)).kind == "video"


@pytest.mark.asyncio
@pytest.mark.skipif(not _has_encoder("libx264"), reason="this ffmpeg can't write H.264")
async def test_a_video_that_doesnt_say_how_long_it_is_is_refused(tmp_path):
    # Written to a pipe, Matroska has no length in it (as some browser recordings)
    path = tmp_path / "recording.mkv"
    with open(path, "wb") as f:
        subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=96x64:d=1:r=10",
                        "-c:v", "libx264", "-f", "matroska", "-"], stdout=f, check=True)
    with pytest.raises(media_assets.UnknownLength):
        await media_assets.probe_asset(path)


@pytest.mark.asyncio
async def test_media_left_preparing_when_the_app_quit_is_prepared_at_start(prores_alpha, db_sessionmaker):
    facts = await media_assets.probe_asset(prores_alpha)
    async with db_sessionmaker() as s:
        project = Project(name="Drama")
        s.add(project)
        await s.flush()
        asset = MediaAsset(project_id=project.id, name="card.mov", kind=facts.kind, path=str(prores_alpha),
                           width=facts.width, height=facts.height, duration_secs=facts.duration_secs,
                           has_alpha=facts.has_alpha, alpha_kept=facts.alpha_kept, has_audio=facts.has_audio,
                           status="preparing")
        s.add(asset)
        await s.commit()
        asset_id = asset.id
    await media_assets.resume_interrupted(db_sessionmaker)
    async with db_sessionmaker() as s:
        assert (await s.get(MediaAsset, asset_id)).status == "ready"


@pytest.mark.asyncio
async def test_a_file_that_isnt_a_picture_is_refused_even_named_like_one(tmp_path):
    path = tmp_path / "notes.png"
    path.write_text("not a picture")
    with pytest.raises(ValueError):
        await media_assets.probe_asset(path)
