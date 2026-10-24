"""
Timeline 0.3.2 — a picture strip: small frames of the video in a row above the
tracks, to find scenes. Made in the background with the bundled ffmpeg and
cached in the episode's folder; never blocks the editor.
"""
import asyncio
import subprocess

import pytest

from app.api.routes import jobs as jobs_routes
from app.core.binaries import ffmpeg, find
from app.models.models import Job
from app.services import picture_strip
from app.tests.timeline_helpers import _make_job

needs_ffmpeg = pytest.mark.skipif(not find("ffmpeg"), reason="needs ffmpeg")


def _video(path, seconds=6):
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=320x240:rate=25",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def test_a_long_episode_is_capped():
    assert picture_strip.frame_interval(6) == 1.0
    assert picture_strip.frame_interval(3600) == 6.0
    assert 3600 / picture_strip.frame_interval(3600) <= picture_strip.MAX_FRAMES
    assert picture_strip.frame_interval(0) == 1.0


@needs_ffmpeg
def test_frames_are_made_in_a_folder_of_their_own(tmp_path):
    video = _video(tmp_path / "v.mp4")
    count = asyncio.run(picture_strip.make_frames(str(video), tmp_path / "frames", 2.0))
    assert 3 <= count <= 4
    assert len(list((tmp_path / "frames").glob("f_*.jpg"))) == count
    assert picture_strip.frames_state(tmp_path / "frames") == {"interval": 2.0, "count": count}


@needs_ffmpeg
@pytest.mark.asyncio
async def test_frames_are_made_once_and_listed(client, db_sessionmaker, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_routes.settings, "UPLOAD_DIR", str(tmp_path))
    job_id = await _make_job(client, db_sessionmaker)
    async with db_sessionmaker() as s:
        job = await s.get(Job, job_id)
        folder = tmp_path / job.project_id / job_id
        folder.mkdir(parents=True)
        job.video_path = str(_video(folder / "source.mp4"))
        job.duration_secs = 6.0
        await s.commit()
    first = (await client.get(f"/api/v1/jobs/{job_id}/frames")).json()
    assert first["interval"] == 1.0
    # The background task ran with the request (ASGI test transport): ready now
    second = (await client.get(f"/api/v1/jobs/{job_id}/frames")).json()
    assert second["ready"] is True and 6 <= len(second["urls"]) <= 7
    assert second["urls"][0].startswith("/uploads/") and second["urls"][0].endswith("f_00001.jpg")


@pytest.mark.asyncio
async def test_a_broken_video_never_fails_the_route(client, db_sessionmaker, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_routes.settings, "UPLOAD_DIR", str(tmp_path))
    job_id = await _make_job(client, db_sessionmaker)
    async with db_sessionmaker() as s:
        job = await s.get(Job, job_id)
        folder = tmp_path / job.project_id / job_id
        folder.mkdir(parents=True)
        (folder / "source.mp4").write_bytes(b"not a video")
        job.video_path = str(folder / "source.mp4")
        job.duration_secs = 6.0
        await s.commit()
    for _ in range(2):
        resp = await client.get(f"/api/v1/jobs/{job_id}/frames")
        assert resp.status_code == 200 and resp.json()["ready"] is False
