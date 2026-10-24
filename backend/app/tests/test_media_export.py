"""Media (0.4.0) in the export: drawn over the picture with its transparency, its sound mixed in."""
import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app.core.binaries import ffmpeg, find
from app.services import audio_extractor as ax
from app.services import media as media_rules

BASE = (105, 136, 115)


def _ff(*args):
    subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture
def base_video(tmp_path) -> Path:
    """4 s of one colour with silent stereo sound — every pixel is BASE."""
    path = tmp_path / "episode.mov"
    _ff("-f", "lavfi", "-i", "color=c=0x698873:s=64x128:d=4:r=10", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-t", "4", "-c:v", "png", "-c:a", "pcm_s16le", str(path))
    return path


@pytest.fixture
def voice(tmp_path) -> dict:
    path = tmp_path / "voice.wav"
    sf.write(str(path), np.zeros(4800, dtype="float32"), 48000)    # one silent line: the mix needs a voice
    return {"start_time": 0.0, "audio_path": str(path), "duration": 0.1}


@pytest.fixture
def card(tmp_path) -> Path:
    """White at alpha 0.72, like the sponsor card's panel."""
    path = tmp_path / "card.mov"
    _ff("-f", "lavfi", "-i", "color=c=white:s=64x128:d=1:r=10", "-vf", "format=rgba,colorchannelmixer=aa=0.72",
        "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", str(path))
    return path


@pytest.fixture
def jingle(tmp_path) -> Path:
    """1 s of a mono 1 kHz tone (lavfi's sine is 1/8 full scale) halved, with a picture: RMS 0.0625/√2."""
    path = tmp_path / "jingle.mov"
    _ff("-f", "lavfi", "-i", "color=c=black:s=64x128:d=1:r=10", "-f", "lavfi", "-i", "sine=f=1000:d=1:sample_rate=48000",
        "-af", "volume=0.5", "-ac", "1", "-c:v", "png", "-c:a", "pcm_s16le", "-shortest", str(path))
    return path


def _clip(path, kind="video", start=1.0, end=2.0, draw=True, sound=False, **kw):
    return {"kind": kind, "path": str(path), "source_in": 0.0, "length": end - start, "start": start, "end": end,
            "x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0, "draw": draw, "sound": sound, "volume_db": 0.0,
            "channels": 1, **kw}


async def _export(base_video, voice, tmp_path, media):
    out = tmp_path / "out.mp4"
    await ax.mix_dubbed_audio(str(base_video), [voice], str(out), media=media, video_width=64, video_height=128)
    return out


def _pixel(video: Path, t: float) -> tuple[int, int, int]:
    raw = subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1",
                          "-vf", "crop=2:2:31:63,format=rgb24", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return raw[0], raw[1], raw[2]


def _near(px, want, tol=8):
    return all(abs(a - b) <= tol for a, b in zip(px, want))


needs_ffmpeg = pytest.mark.skipif(not find("ffmpeg"), reason="needs ffmpeg")


@needs_ffmpeg
@pytest.mark.asyncio
async def test_a_transparent_clip_is_blended_over_the_picture_only_in_its_window(base_video, voice, card, tmp_path):
    out = await _export(base_video, voice, tmp_path, [_clip(card)])
    blend = tuple(round(0.722 * 255 + 0.278 * c) for c in BASE)
    assert _near(_pixel(out, 1.5), blend)
    assert _near(_pixel(out, 0.5), BASE) and _near(_pixel(out, 2.5), BASE)


@needs_ffmpeg
@pytest.mark.asyncio
async def test_a_hidden_track_is_not_drawn(base_video, voice, card, tmp_path):
    out = await _export(base_video, voice, tmp_path, [_clip(card, draw=False)])
    assert _near(_pixel(out, 1.5), BASE)


@needs_ffmpeg
@pytest.mark.asyncio
async def test_an_image_stays_for_its_whole_clip(base_video, voice, tmp_path):
    from PIL import Image
    png = tmp_path / "white.png"
    Image.new("RGBA", (16, 16), (255, 255, 255, 255)).save(png)
    out = await _export(base_video, voice, tmp_path, [_clip(png, kind="image", start=0.5, end=3.5)])
    assert _near(_pixel(out, 1.0), (255, 255, 255)) and _near(_pixel(out, 3.2), (255, 255, 255))
    assert _near(_pixel(out, 3.8), BASE)


def _levels(video: Path, tmp_path) -> tuple[np.ndarray, int]:
    wav = tmp_path / "out.wav"
    _ff("-i", str(video), "-ac", "2", "-ar", "48000", str(wav))
    return sf.read(str(wav), dtype="float32")


def _rms(samples, sr, a, b):
    return np.sqrt(np.mean(samples[int(a * sr):int(b * sr)] ** 2, axis=0))


@needs_ffmpeg
@pytest.mark.asyncio
async def test_a_clips_sound_plays_in_its_window_in_both_channels(base_video, voice, jingle, tmp_path):
    out = await _export(base_video, voice, tmp_path, [_clip(jingle, start=2.0, end=3.0, draw=False, sound=True)])
    samples, sr = _levels(out, tmp_path)
    left, right = _rms(samples, sr, 2.2, 2.8)
    assert left == pytest.approx(0.125 * 0.5 / np.sqrt(2), rel=0.15) and right == pytest.approx(left, rel=0.05)
    assert max(_rms(samples, sr, 0.3, 1.7)) < 0.01


@needs_ffmpeg
@pytest.mark.asyncio
async def test_a_muted_track_is_not_heard(base_video, voice, jingle, tmp_path):
    out = await _export(base_video, voice, tmp_path, [_clip(jingle, start=2.0, end=3.0, draw=False, sound=False)])
    samples, sr = _levels(out, tmp_path)
    assert max(_rms(samples, sr, 2.2, 2.8)) < 0.01


def test_an_export_takes_at_most_32_media_clips():
    assert media_rules.export_limit_message(32) is None
    assert media_rules.export_limit_message(33) == "This episode has 33 media clips; at most 32 can be exported."


@pytest.mark.asyncio
async def test_the_export_gets_media_and_no_longer_the_old_overlays(monkeypatch, client, db_sessionmaker, tmp_path):
    import app.core.database as core_db
    from app.api.routes import tts as tts_routes
    from app.models.models import Job, JobStatus, MediaAsset, MediaClip, Overlay, Segment
    from app.tests.timeline_helpers import _make_job, _write_clip

    monkeypatch.setattr(core_db, "AsyncSessionLocal", db_sessionmaker)
    job_id = await _make_job(client, db_sessionmaker)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    card = tmp_path / "card.mov"
    card.write_bytes(b"x")
    async with db_sessionmaker() as s:
        job = await s.get(Job, job_id)
        job.video_path = str(video)
        job.media_track_flags = '{"1": {"hidden": true}}'
        seg = Segment(job_id=job_id, start_time=0.0, end_time=1.0, khmer_text="ក", tts_duration_secs=1.0)
        s.add(seg)
        await s.flush()
        seg.tts_audio_path = _write_clip(tmp_path, seg.id, 1.0)
        asset = MediaAsset(project_id=job.project_id, name="card.mov", kind="video", path=str(card), width=1080,
                           height=1920, duration_secs=7.0, has_alpha=True, alpha_kept=True, has_audio=True, status="ready")
        s.add(asset)
        await s.flush()
        s.add(MediaClip(job_id=job_id, asset_id=asset.id, lane_index=0, start_time=2.0, end_time=9.0))
        s.add(MediaClip(job_id=job_id, asset_id=asset.id, lane_index=1, start_time=20.0, end_time=27.0, source_in=1.0))
        s.add(Overlay(job_id=job_id, type="shape", blur=True))
        job.status = JobStatus.MIXING
        await s.commit()
    seen = {}

    async def fake_probe(path):
        return {"streams": [{"codec_type": "video", "width": 720, "height": 1280}, {"codec_type": "audio", "channels": 2}]}

    async def fake_mix(video_path, tts_segments, output_path, **kwargs):
        seen.update(kwargs)
        return output_path

    monkeypatch.setattr(tts_routes, "probe_video", fake_probe)
    monkeypatch.setattr(tts_routes, "mix_dubbed_audio", fake_mix)
    await tts_routes._mix_job_task(job_id, True)
    assert not seen.get("video_layers")                                    # the old overlays are gone
    shown, hidden = seen["media"]
    assert (shown["start"], shown["end"], shown["draw"], shown["sound"]) == (2.0, 9.0, True, True)
    assert (hidden["source_in"], hidden["draw"], hidden["sound"], hidden["channels"]) == (1.0, False, True, 2)
    assert (seen["video_width"], seen["video_height"]) == (720, 1280)


def test_a_new_upload_no_longer_gets_the_old_overlay_template():
    from app.api.routes import jobs as jobs_routes
    assert not hasattr(jobs_routes, "apply_default_overlay_template")


@pytest.mark.asyncio
async def test_32_media_clips_and_a_full_episode_stay_within_the_open_file_limit(monkeypatch, tmp_path):
    commands = []

    class _Proc:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_exec(*cmd, **kwargs):
        commands.append(list(cmd))
        return _Proc()

    monkeypatch.setattr(ax.asyncio, "create_subprocess_exec", fake_exec)
    clips = [{"start_time": i * 0.5, "audio_path": f"/clips/{i}.wav", "duration": 0.4} for i in range(3000)]
    media = [_clip(f"/media/{i}.mov", start=i * 3.0, end=i * 3.0 + 2.0, sound=True) for i in range(media_rules.MAX_EXPORT_CLIPS)]
    await ax.mix_dubbed_audio("/in.mp4", clips, str(tmp_path / "out.mp4"), media=media, video_width=640, video_height=360)
    inputs_per_run = [c.count("-i") for c in commands if c[0].endswith("ffmpeg")]
    assert max(inputs_per_run) <= ax.DUB_BATCH + 4 + media_rules.MAX_EXPORT_CLIPS
