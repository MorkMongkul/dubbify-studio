"""
When the free separation service is asleep or slow, the original audio stands
in for both the music and the voices track. That used to be reported once, in
a toast that the next stage cleared, with no way to retry short of uploading
again. Now the job says so for as long as it's true (stems_separated), and
"Try again" re-runs separation without touching the episode's lines.
"""
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.binaries import ffmpeg, find
from app.core.config import settings
from app.models.models import Job, JobStatus, Project, Segment
from app.services import pipeline, source_separator

pytestmark = pytest.mark.skipif(not find("ffmpeg"), reason="needs ffmpeg")


def _tone(path: Path, seconds: float = 1.0) -> None:
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f=300:d={seconds}",
                    "-ac", "2", "-ar", "44100", str(path)], check=True)


def _space_down(monkeypatch):
    def down(path):
        raise ConnectionError("Space is sleeping")
    monkeypatch.setattr(source_separator, "_hf_separate_call", down)
    monkeypatch.setattr(source_separator.settings, "SEPARATION_HF_SPACE", "someone/separator")


def _space_up(monkeypatch, tmp_path):
    vocals, music = tmp_path / "sep_vocals.mp3", tmp_path / "sep_music.mp3"
    _tone(vocals)
    _tone(music)
    monkeypatch.setattr(source_separator, "_hf_separate_call", lambda path: (str(vocals), str(music)))
    monkeypatch.setattr(source_separator.settings, "SEPARATION_HF_SPACE", "someone/separator")


@pytest.mark.asyncio
async def test_a_failed_separation_is_remembered_and_try_again_clears_it(monkeypatch, tmp_path):
    audio = tmp_path / "mix.wav"
    _tone(audio)
    out = tmp_path / "job"
    out.mkdir()

    _space_down(monkeypatch)
    _, _, degraded = await source_separator.separate_vocals_bgm(str(audio), str(out), 60.0)
    assert degraded
    assert "Space is sleeping" in (out / source_separator.UNAVAILABLE_MARKER).read_text()
    # Running stage 1 again keeps reporting it instead of pretending it worked.
    assert (await source_separator.separate_vocals_bgm(str(audio), str(out)))[2]

    _space_up(monkeypatch, tmp_path)
    _, _, degraded = await source_separator.separate_vocals_bgm(str(audio), str(out), 60.0, force=True)
    assert not degraded
    assert not (out / source_separator.UNAVAILABLE_MARKER).exists()
    assert (out / "no_vocals.m4a").exists()


def test_long_episodes_get_more_time():
    assert source_separator.separation_timeout(0) == 300             # 5 minutes at least
    assert source_separator.separation_timeout(10 * 60) == 1020      # a 10-minute episode: 17 minutes
    assert source_separator.separation_timeout(45 * 60) == 1800      # never more than 30 minutes


@pytest.mark.asyncio
async def test_try_again_keeps_the_lines_and_reports_success(client, db_sessionmaker, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    video = tmp_path / "ep.mp4"
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=s=160x90:d=1",
                    "-f", "lavfi", "-i", "sine=f=300:d=1", "-shortest", "-c:v", "mpeg4", "-c:a", "aac",
                    str(video)], check=True)
    async with db_sessionmaker() as db:
        project = Project(name="Ep")
        db.add(project)
        await db.flush()
        job = Job(project_id=project.id, video_path=str(video), status=JobStatus.COMPLETED, duration_secs=1.0)
        db.add(job)
        await db.flush()
        db.add(Segment(job_id=job.id, start_time=0, end_time=1, khmer_text="សួស្តី"))
        await db.commit()
        job_id, project_id = job.id, project.id
    job_dir = tmp_path / "uploads" / project_id / job_id
    job_dir.mkdir(parents=True)
    _tone(job_dir / "no_vocals.wav")
    (job_dir / source_separator.UNAVAILABLE_MARKER).write_text("Space is sleeping\n")
    assert (await client.get(f"/api/v1/jobs/{job_id}")).json()["stems_separated"] is False

    # The background task runs against the test database.
    monkeypatch.setattr("app.core.database.AsyncSessionLocal", db_sessionmaker)
    _space_up(monkeypatch, tmp_path)
    resp = await client.post(f"/api/v1/jobs/{job_id}/separate")
    assert resp.status_code == 202

    body = (await client.get(f"/api/v1/jobs/{job_id}")).json()
    assert body["status"] == "completed" and body["error_msg"] == ""
    assert body["stems_separated"] is True
    async with db_sessionmaker() as db:
        lines = (await db.execute(select(Segment).where(Segment.job_id == job_id))).scalars().all()
    assert [s.khmer_text for s in lines] == ["សួស្តី"]


@pytest.mark.asyncio
async def test_try_again_waits_for_a_busy_episode(client, db_sessionmaker):
    async with db_sessionmaker() as db:
        project = Project(name="Ep")
        db.add(project)
        await db.flush()
        job = Job(project_id=project.id, video_path="/nowhere.mp4", status=JobStatus.MIXING)
        db.add(job)
        await db.commit()
        job_id = job.id
    assert (await client.post(f"/api/v1/jobs/{job_id}/separate")).status_code == 409


@pytest.mark.asyncio
async def test_a_failed_try_again_leaves_the_episode_as_it_was(monkeypatch, tmp_path):
    """The working stems are only replaced once a separation has succeeded."""
    audio = tmp_path / "mix.wav"
    _tone(audio)
    out = tmp_path / "job"
    out.mkdir()
    _space_down(monkeypatch)
    await source_separator.separate_vocals_bgm(str(audio), str(out), 60.0)
    before = {name: (out / name).read_bytes() for name in ("vocals.wav", "no_vocals.wav")}

    def busy(path):
        # Mid-attempt, where quitting the app would leave things: still the working stems
        assert (out / "no_vocals.wav").read_bytes() == before["no_vocals.wav"]
        raise ConnectionError("Space is busy")
    monkeypatch.setattr(source_separator, "_hf_separate_call", busy)
    _, _, degraded = await source_separator.separate_vocals_bgm(str(audio), str(out), 60.0, force=True)
    assert degraded
    assert {name: (out / name).read_bytes() for name in before} == before
    assert "Space is busy" in (out / source_separator.UNAVAILABLE_MARKER).read_text()
    assert not [p for p in out.iterdir() if p.name.startswith("separating-")]


@pytest.mark.asyncio
async def test_a_successful_try_again_keeps_the_music_level_on_the_new_stem(db_sessionmaker, monkeypatch, tmp_path):
    """
    Changing the music level renders no_vocals.wav from no_vocals_raw.wav. That
    copy still held the unseparated mix after a retry, so the next level change
    brought the original voices back.
    """
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr("app.core.database.AsyncSessionLocal", db_sessionmaker)
    video = tmp_path / "ep.mp4"
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=s=160x90:d=1",
                    "-f", "lavfi", "-i", "sine=f=300:d=1", "-shortest", "-c:v", "mpeg4", "-c:a", "aac",
                    str(video)], check=True)
    async with db_sessionmaker() as db:
        project = Project(name="Ep")
        db.add(project)
        await db.flush()
        job = Job(project_id=project.id, video_path=str(video), status=JobStatus.SEPARATING,
                  duration_secs=1.0, bgm_volume_db=-12.0)
        db.add(job)
        await db.commit()
        job_id, project_id = job.id, project.id
    job_dir = tmp_path / "uploads" / project_id / job_id
    job_dir.mkdir(parents=True)
    for name in ("vocals.wav", "no_vocals.wav", "no_vocals_raw.wav"):
        _tone(job_dir / name)                     # the original audio standing in
    (job_dir / source_separator.UNAVAILABLE_MARKER).write_text("Space is sleeping\n")
    old_raw = (job_dir / "no_vocals_raw.wav").read_bytes()

    music = tmp_path / "sep_music.wav"
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=1000:d=1",
                    "-ac", "2", "-ar", "44100", str(music)], check=True)
    vocals = tmp_path / "sep_vocals.wav"
    _tone(vocals)
    monkeypatch.setattr(source_separator, "_hf_separate_call", lambda path: (str(vocals), str(music)))
    monkeypatch.setattr(source_separator.settings, "SEPARATION_HF_SPACE", "someone/separator")
    await pipeline.run_separation_retry(job_id, JobStatus.COMPLETED)

    raw = (job_dir / "no_vocals_raw.wav").read_bytes()
    assert raw != old_raw                         # the level is applied to the new stem
    assert (job_dir / "no_vocals.wav").read_bytes() != raw
    assert not (job_dir / source_separator.UNAVAILABLE_MARKER).exists()


@pytest.mark.asyncio
async def test_export_waits_for_a_separation_retry(client, db_sessionmaker):
    """The music track is about to change: exporting now used the original audio instead."""
    async with db_sessionmaker() as db:
        project = Project(name="Ep")
        db.add(project)
        await db.flush()
        job = Job(project_id=project.id, video_path="/nowhere.mp4", status=JobStatus.SEPARATING)
        db.add(job)
        await db.flush()
        db.add(Segment(job_id=job.id, start_time=0, end_time=1, khmer_text="សួស្តី", tts_audio_path="/v/1.wav"))
        await db.commit()
        job_id = job.id
    assert (await client.post(f"/api/v1/tts/mix/{job_id}", json={})).status_code == 409
