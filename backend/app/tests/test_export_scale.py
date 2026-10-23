"""
A full episode has hundreds of lines. Export used to give ffmpeg one input
per voice clip and one per subtitle line; an app opened from Finder may only
have 256 files open, so full episodes failed with "Too many open files".
Voices are now mixed in batches and every subtitle line goes into one image
stream, so no ffmpeg run needs more than about a batch of files.
"""
import asyncio
import json
import resource
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.binaries import ffmpeg, ffprobe, find
from app.services import audio_extractor as ax
from app.services.video_overlay import build_subtitle_track, render_subtitle_pngs


class _Proc:
    returncode = 0

    async def communicate(self):
        return b"", b""


@pytest.mark.asyncio
async def test_no_ffmpeg_run_opens_more_than_a_batch_of_files(monkeypatch, tmp_path):
    commands = []

    async def fake_exec(*cmd, **kwargs):
        commands.append(list(cmd))
        return _Proc()

    monkeypatch.setattr(ax.asyncio, "create_subprocess_exec", fake_exec)
    clips = [{"start_time": i * 0.5, "audio_path": f"/clips/{i}.wav", "duration": 0.4} for i in range(3000)]
    subtitles = {"kind": "sequence", "concat_path": "/subs/track.ffconcat", "left_px": 0, "top_px": 300}
    await ax.mix_dubbed_audio("/in.mp4", clips, str(tmp_path / "out.mp4"),
                              video_layers=[subtitles], video_width=640, video_height=360)

    inputs_per_run = [c.count("-i") for c in commands if c[0].endswith("ffmpeg")]
    assert max(inputs_per_run) <= ax.DUB_BATCH + 4
    # Every clip is an input of exactly one batch run.
    clip_inputs = [c[i + 1] for c in commands for i, a in enumerate(c) if a == "-i" and c[i + 1].startswith("/clips/")]
    assert sorted(clip_inputs) == sorted(c["audio_path"] for c in clips)


def test_overlapping_lines_stack_and_gaps_are_empty(tmp_path):
    segments = [
        SimpleNamespace(id="a", start_time=1.0, end_time=3.0, khmer_text="សួស្តី"),
        SimpleNamespace(id="b", start_time=2.0, end_time=4.0, khmer_text="អរគុណ"),
        SimpleNamespace(id="c", start_time=6.0, end_time=7.5, khmer_text="លាហើយ"),
    ]
    box = {"x": 0.05, "y": 0.8, "width": 0.9, "height": 0.15}
    lines = render_subtitle_pngs(segments, box, 640, 360, tmp_path / "lines")
    track = build_subtitle_track(lines, 640, 360, tmp_path / "track")
    body = Path(track["concat_path"]).read_text().splitlines()
    durations = [float(line.split()[1]) for line in body if line.startswith("duration")]
    files = [line.split(" ", 1)[1] for line in body if line.startswith("file")]
    # blank, a, a+b, b, blank, c, then the closing blank frames
    assert [round(d, 3) for d in durations[:6]] == [1.0, 1.0, 1.0, 1.0, 2.0, 1.5]
    assert files[0] == files[4] and "blank" in files[0]
    assert len({f for f in files if "frame_" in f}) == 4


@pytest.mark.skipif(not find("ffmpeg"), reason="needs ffmpeg")
def test_a_full_episode_exports_within_the_finder_file_limit(tmp_path):
    work = tmp_path
    clip = work / "line.wav"
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=0.25", "-ac", "1", str(clip)], check=True)
    video = work / "in.mp4"
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x101830:s=320x180:d=8:r=10",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "8", "-shortest",
                    "-c:v", "mpeg4", "-c:a", "aac", str(video)], check=True)
    clips = [{"start_time": round(i * 0.015, 3), "audio_path": str(clip), "duration": 0.25} for i in range(300)]   # 0–4.7 s
    segments = [SimpleNamespace(id=f"s{i}", start_time=i * 0.035, end_time=i * 0.035 + 0.03, khmer_text="សួស្តី")
                for i in range(200)]
    lines = render_subtitle_pngs(segments, {"x": 0.05, "y": 0.75, "width": 0.9, "height": 0.2}, 320, 180, work / "lines")
    track = build_subtitle_track(lines, 320, 180, work / "track")

    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, hard))     # tighter than an app opened from Finder
    try:
        out = asyncio.run(ax.mix_dubbed_audio(str(video), clips, str(work / "out.mp4"),
                                              video_layers=[track], video_width=320, video_height=180))
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))

    probe = json.loads(subprocess.run([ffprobe(), "-v", "error", "-show_streams", "-show_format", "-of", "json", out],
                                      capture_output=True, text=True, check=True).stdout)
    assert {s["codec_type"] for s in probe["streams"]} == {"video", "audio"}
    assert abs(float(probe["format"]["duration"]) - 8.0) < 0.3
    # The voices are there: loud while the clips play, silent after them.
    def peak(start):
        r = subprocess.run([ffmpeg(), "-v", "info", "-ss", str(start), "-t", "1", "-i", out, "-af", "volumedetect",
                            "-f", "null", "-"], capture_output=True, text=True)
        return float(r.stderr.split("max_volume:")[1].split("dB")[0])
    assert peak(1.0) > -20 and peak(6.0) < -60
    assert not (work / "mix_parts").exists()


@pytest.mark.skipif(not find("ffmpeg"), reason="needs ffmpeg")
@pytest.mark.asyncio
async def test_a_failing_progress_update_does_not_stop_the_encode():
    """Progress is written to the database; a failed write used to abandon ffmpeg mid-export."""
    calls = []

    async def broken(fraction):
        calls.append(fraction)
        raise RuntimeError("database is locked")

    rc, _ = await ax._run_ffmpeg(
        [ffmpeg(), "-v", "error", "-f", "lavfi", "-i", "sine=d=2", "-f", "null", "-"],
        duration=2.0, on_progress=broken,
    )
    assert rc == 0 and calls


@pytest.mark.skipif(not find("ffmpeg"), reason="needs ffmpeg")
def test_the_exported_voice_is_as_loud_as_in_the_preview(tmp_path):
    """
    The mono voice track was mixed into the stereo music at -3 dB per side
    (ffmpeg's mono-to-stereo pan law), while the preview plays the same clip
    at full level on both sides: every export's voices sat 3 dB lower against
    the music than what the user heard while editing.
    """
    clip = tmp_path / "line.wav"
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=2", "-ac", "1",
                    "-af", "volume=-17dB", str(clip)], check=True)          # a sine at -17 dB peak is about -20 dB RMS
    music = tmp_path / "no_vocals.wav"
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "4",
                    str(music)], check=True)
    video = tmp_path / "in.mp4"
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=160x90:d=4:r=10",
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "4", "-shortest",
                    "-c:v", "mpeg4", "-c:a", "aac", str(video)], check=True)
    out = asyncio.run(ax.mix_dubbed_audio(str(video), [{"start_time": 1.0, "audio_path": str(clip), "duration": 2.0}],
                                          str(tmp_path / "out.mp4"), bgm_path=str(music)))

    def rms(path, channel=None):
        af = "volumedetect" if channel is None else f"pan=mono|c0=c{channel},volumedetect"
        r = subprocess.run([ffmpeg(), "-v", "info", "-ss", "1.5", "-t", "1", "-i", str(path), "-af", af, "-f", "null", "-"],
                           capture_output=True, text=True)
        return float(r.stderr.split("mean_volume:")[1].split("dB")[0])

    clip_level = float(subprocess.run([ffmpeg(), "-v", "info", "-i", str(clip), "-af", "volumedetect", "-f", "null", "-"],
                                      capture_output=True, text=True).stderr.split("mean_volume:")[1].split("dB")[0])
    for side in (0, 1):
        assert rms(out, side) == pytest.approx(clip_level, abs=1.0)


# ── 0.3.0: the export plays each clip's window, as the preview does ───────────

def _clip_with_lead(path, lead: float, tone: float, rate: int = 48000):
    import numpy as np
    import soundfile as sf
    t = np.arange(int(rate * tone)) / rate
    sf.write(str(path), np.concatenate([np.zeros(int(rate * lead), dtype=np.float32),
                                        (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)]), rate)
    return str(path)


def _rms_db(samples) -> float:
    import numpy as np
    return float(20 * np.log10(np.sqrt(np.mean(np.square(samples))) + 1e-12))


@pytest.mark.skipif(not find("ffmpeg"), reason="needs ffmpeg")
def test_export_plays_only_the_clips_window(tmp_path):
    import soundfile as sf
    clip = _clip_with_lead(tmp_path / "clip.wav", 0.5, 1.5)
    parts = asyncio.run(ax.build_dub_parts(
        [{"start_time": 3.0, "audio_path": clip, "duration": 2.0, "offset": 0.5, "length": 1.0, "fit_rate": 1.0}],
        tmp_path / "work"))
    (part, offset), = parts
    assert offset == 3.0
    data, rate = sf.read(part, dtype="float32")
    assert len(data) / rate == pytest.approx(1.0, abs=0.03)
    # Starts on the kept sound, not on the silence that was cut off
    assert _rms_db(data[: int(0.05 * rate)]) > -30


@pytest.mark.skipif(not find("ffmpeg"), reason="needs ffmpeg")
def test_export_applies_a_kept_fit_rate(tmp_path):
    import soundfile as sf
    clip = _clip_with_lead(tmp_path / "clip.wav", 0.0, 1.5)
    (part, _), = asyncio.run(ax.build_dub_parts(
        [{"start_time": 0.0, "audio_path": clip, "duration": 1.5, "offset": 0.0, "length": 1.0, "fit_rate": 1.5}],
        tmp_path / "work"))
    assert sf.info(part).duration == pytest.approx(1.0, abs=0.03)


@pytest.mark.asyncio
async def test_the_export_is_given_each_clips_window(monkeypatch, client, db_sessionmaker, tmp_path):
    """The clips the export mixes carry exactly what the preview plays."""
    import app.core.database as core_db
    from app.api.routes import tts as tts_routes
    from app.models.models import Job, JobStatus, Segment
    from app.tests.timeline_helpers import _make_job, _write_clip

    monkeypatch.setattr(core_db, "AsyncSessionLocal", db_sessionmaker)
    job_id = await _make_job(client, db_sessionmaker)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    async with db_sessionmaker() as s:
        job = await s.get(Job, job_id)
        job.video_path = str(video)
        seg = Segment(job_id=job_id, start_time=2.0, end_time=3.0, khmer_text="ក", tts_duration_secs=2.5,
                      voice_offset=0.4, fit_rate=1.2)
        s.add(seg)
        await s.flush()
        seg.tts_audio_path = _write_clip(tmp_path, seg.id, 2.5)
        job.status = JobStatus.MIXING
        await s.commit()
    seen = {}

    async def fake_mix(video_path, tts_segments, output_path, **kwargs):
        seen["clips"] = tts_segments
        return output_path

    monkeypatch.setattr(tts_routes, "mix_dubbed_audio", fake_mix)
    await tts_routes._mix_job_task(job_id, True)
    (clip,) = seen["clips"]
    assert {k: clip[k] for k in ("start_time", "offset", "length", "fit_rate")} == {
        "start_time": 2.0, "offset": 0.4, "length": pytest.approx(1.0), "fit_rate": 1.2}


# ── 0.3.1: fades and the music dipping under voices ───────────────────────────

def _decoded(path, tmp_path, name="out.wav"):
    import soundfile as sf
    wav = tmp_path / name
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-i", str(path), "-ac", "1", "-ar", "48000", str(wav)], check=True)
    return sf.read(str(wav), dtype="float32")


@pytest.mark.skipif(not find("ffmpeg"), reason="needs ffmpeg")
def test_export_fades_the_clips_window(tmp_path):
    import soundfile as sf
    clip = _clip_with_lead(tmp_path / "clip.wav", 0.0, 1.0)
    (part, _), = asyncio.run(ax.build_dub_parts(
        [{"start_time": 0.0, "audio_path": clip, "duration": 1.0, "offset": 0.0, "length": 1.0, "fit_rate": 1.0,
          "fade_in": 0.5, "fade_out": 0.2}],
        tmp_path / "work"))
    data, rate = sf.read(part, dtype="float32")
    first = _rms_db(data[: int(0.1 * rate)])
    middle = _rms_db(data[int(0.6 * rate): int(0.75 * rate)])
    last = _rms_db(data[int(0.95 * rate):])
    assert middle - first > 12
    assert middle - last > 12


@pytest.mark.skipif(not find("ffmpeg"), reason="needs ffmpeg")
def test_export_ducks_the_music_under_a_voice(tmp_path):
    import numpy as np
    import soundfile as sf
    from app.services.timeline import duck_volume_expr
    video = tmp_path / "v.mp4"
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x240:d=4",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "4", "-c:v", "libx264", "-c:a", "aac",
                    "-shortest", str(video)], check=True)
    t = np.arange(4 * 48000) / 48000
    bgm = tmp_path / "bgm.wav"
    sf.write(str(bgm), np.stack([0.2 * np.sin(2 * np.pi * 440 * t)] * 2, axis=1).astype(np.float32), 48000)
    voice = tmp_path / "voice.wav"
    sf.write(str(voice), np.zeros(48000, dtype=np.float32), 48000)    # a silent voice: only the music is heard
    out = tmp_path / "out.mp4"
    asyncio.run(ax.mix_dubbed_audio(
        str(video), [{"start_time": 1.5, "audio_path": str(voice), "duration": 1.0, "offset": 0.0, "length": 1.0,
                      "fit_rate": 1.0}],
        str(out), bgm_path=str(bgm), duck_expr=duck_volume_expr([(1.5, 2.5)])))
    data, rate = _decoded(out, tmp_path)
    before = _rms_db(data[int(0.2 * rate): int(1.0 * rate)])
    under = _rms_db(data[int(1.7 * rate): int(2.3 * rate)])
    assert under - before == pytest.approx(-8, abs=1)


@pytest.mark.asyncio
async def test_an_older_episode_exports_without_ducking(monkeypatch, client, db_sessionmaker, tmp_path):
    seen = await _export_job(monkeypatch, client, db_sessionmaker, tmp_path, duck_music=None)
    assert seen["duck_expr"] is None
    seen = await _export_job(monkeypatch, client, db_sessionmaker, tmp_path, duck_music=True)
    assert seen["duck_expr"].startswith("1-") and "clip(" in seen["duck_expr"]
    (clip,) = seen["clips"]
    assert (clip["fade_in"], clip["fade_out"]) == (0.25, 0.1)


async def _export_job(monkeypatch, client, db_sessionmaker, tmp_path, duck_music, with_pieces=False,
                      music=None, voices=None):
    import app.core.database as core_db
    from app.api.routes import tts as tts_routes
    from app.models.models import Job, JobStatus, Segment
    from app.tests.timeline_helpers import _make_job, _write_clip

    monkeypatch.setattr(core_db, "AsyncSessionLocal", db_sessionmaker)
    job_id = await _make_job(client, db_sessionmaker)
    video = tmp_path / f"{job_id}.mp4"
    video.write_bytes(b"x")
    async with db_sessionmaker() as s:
        job = await s.get(Job, job_id)
        job.video_path = str(video)
        job.duck_music = duck_music
        job.duration_secs = 10.0
        seg = Segment(job_id=job_id, start_time=2.0, end_time=3.0, khmer_text="ក", tts_duration_secs=1.0,
                      fade_in_secs=0.25, fade_out_secs=0.1)
        s.add(seg)
        await s.flush()
        seg.tts_audio_path = _write_clip(tmp_path, seg.id, 1.0)
        job.status = JobStatus.MIXING
        await s.commit()
    if with_pieces:   # the editor opened the episode: one whole piece per stem
        await client.get(f"/api/v1/jobs/{job_id}/stem-pieces")
    for stem, pieces in (("music", music), ("voices", voices)):
        if pieces is not None:
            await client.put(f"/api/v1/jobs/{job_id}/stem-pieces/{stem}", json={"pieces": pieces})
    if voices:   # the separated original voices, next to the episode's other files
        from app.api.routes import tts as tts_routes
        monkeypatch.setattr(tts_routes.settings, "UPLOAD_DIR", str(tmp_path))
        async with db_sessionmaker() as s:
            project_id = (await s.get(Job, job_id)).project_id
        (tmp_path / project_id / job_id).mkdir(parents=True, exist_ok=True)
        _tone_wav(tmp_path / project_id / job_id / "vocals.wav", seconds=1)
    seen = {}

    async def fake_mix(video_path, tts_segments, output_path, **kwargs):
        seen["clips"] = tts_segments
        seen.update({k: kwargs.get(k) for k in ("duck_expr", "music_expr", "voices_path", "voices_expr")})
        return output_path

    monkeypatch.setattr(tts_routes, "mix_dubbed_audio", fake_mix)
    await tts_routes._mix_job_task(job_id, True)
    return seen


# ── 0.3.2: the music and the original voices in pieces ────────────────────────

def _silent_video(tmp_path, seconds=4):
    video = tmp_path / "v.mp4"
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black:s=320x240:d={seconds}",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", str(seconds), "-c:v", "libx264",
                    "-c:a", "aac", "-shortest", str(video)], check=True)
    return video


def _tone_wav(path, seconds=4, level=0.2, stereo=True):
    import numpy as np
    import soundfile as sf
    t = np.arange(int(seconds * 48000)) / 48000
    x = (level * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    sf.write(str(path), np.stack([x, x], axis=1) if stereo else x, 48000)
    return path


@pytest.mark.skipif(not find("ffmpeg"), reason="needs ffmpeg")
def test_a_deleted_music_piece_is_silent_in_the_export(tmp_path):
    from types import SimpleNamespace
    import numpy as np
    import soundfile as sf
    from app.services.stem_pieces import pieces_volume_expr
    video = _silent_video(tmp_path)
    bgm = _tone_wav(tmp_path / "bgm.wav")
    voice = tmp_path / "voice.wav"
    sf.write(str(voice), np.zeros(4800, dtype=np.float32), 48000)
    pieces = [SimpleNamespace(start_time=0.0, end_time=1.0, volume_db=0.0, fade_in_secs=0.0, fade_out_secs=0.0, in_export=True),
              SimpleNamespace(start_time=2.0, end_time=4.0, volume_db=0.0, fade_in_secs=0.0, fade_out_secs=0.0, in_export=True)]
    out = tmp_path / "out.mp4"
    asyncio.run(ax.mix_dubbed_audio(
        str(video), [{"start_time": 0.0, "audio_path": str(voice), "duration": 0.1}], str(out),
        bgm_path=str(bgm), music_expr=pieces_volume_expr(pieces, 4.0)))
    data, rate = _decoded(out, tmp_path)
    assert _rms_db(data[int(1.2 * rate): int(1.8 * rate)]) < -60
    assert _rms_db(data[int(2.5 * rate): int(3.5 * rate)]) > -25


@pytest.mark.skipif(not find("ffmpeg"), reason="needs ffmpeg")
def test_a_switched_on_voices_piece_is_exported(tmp_path):
    from types import SimpleNamespace
    import numpy as np
    import soundfile as sf
    from app.services.stem_pieces import pieces_volume_expr
    video = _silent_video(tmp_path)
    bgm = tmp_path / "bgm.wav"
    sf.write(str(bgm), np.zeros((4 * 48000, 2), dtype=np.float32), 48000)
    vocals = _tone_wav(tmp_path / "vocals.wav")
    voice = tmp_path / "voice.wav"
    sf.write(str(voice), np.zeros(4800, dtype=np.float32), 48000)
    pieces = [SimpleNamespace(start_time=0.0, end_time=1.0, volume_db=0.0, fade_in_secs=0.0, fade_out_secs=0.0, in_export=False),
              SimpleNamespace(start_time=1.0, end_time=2.0, volume_db=0.0, fade_in_secs=0.0, fade_out_secs=0.0, in_export=True)]
    out = tmp_path / "out.mp4"
    asyncio.run(ax.mix_dubbed_audio(
        str(video), [{"start_time": 0.0, "audio_path": str(voice), "duration": 0.1}], str(out),
        bgm_path=str(bgm), voices_path=str(vocals), voices_expr=pieces_volume_expr(pieces, 4.0, export_only=True)))
    data, rate = _decoded(out, tmp_path)
    assert _rms_db(data[int(1.2 * rate): int(1.8 * rate)]) > -25
    assert _rms_db(data[int(0.2 * rate): int(0.8 * rate)]) < -60
    assert _rms_db(data[int(2.5 * rate): int(3.5 * rate)]) < -60


@pytest.mark.asyncio
async def test_untouched_pieces_add_no_filter(monkeypatch, client, db_sessionmaker, tmp_path):
    seen = await _export_job(monkeypatch, client, db_sessionmaker, tmp_path, duck_music=None, with_pieces=True)
    assert seen["music_expr"] is None and seen["voices_path"] is None and seen["voices_expr"] is None


@pytest.mark.asyncio
async def test_edited_pieces_reach_the_export(monkeypatch, client, db_sessionmaker, tmp_path):
    seen = await _export_job(monkeypatch, client, db_sessionmaker, tmp_path, duck_music=None, with_pieces=True,
                             music=[{"start_time": 0.0, "end_time": 4.0}],
                             voices=[{"start_time": 1.0, "end_time": 2.0, "in_export": True}])
    assert seen["music_expr"].startswith("gte(t,0.000)")
    assert seen["voices_expr"].startswith("gte(t,1.000)") and seen["voices_path"].endswith("vocals.wav")


@pytest.mark.asyncio
async def test_music_emptied_by_the_user_is_silent_in_the_export(monkeypatch, client, db_sessionmaker, tmp_path):
    seen = await _export_job(monkeypatch, client, db_sessionmaker, tmp_path, duck_music=None, with_pieces=True, music=[])
    assert seen["music_expr"] == "0"
