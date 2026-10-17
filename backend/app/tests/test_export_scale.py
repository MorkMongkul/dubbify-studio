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
