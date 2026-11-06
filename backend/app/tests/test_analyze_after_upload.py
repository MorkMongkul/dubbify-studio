"""
Settings → AI models → After upload (0.5.6): "analyze" starts the analysis as soon as the music is separated, so an
upload is ready to check without a click; "wait" (the default) keeps today's Analyze button. The automatic start
claims the episode like the button does, so a click at the same moment can't start a second run.
"""
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.models import Job, JobStatus, Project
from app.services import pipeline

pytestmark = pytest.mark.asyncio


async def _upload(db_sessionmaker, monkeypatch, tmp_path, after: str, degraded=False):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "AFTER_UPLOAD", after)
    monkeypatch.setattr("app.core.database.AsyncSessionLocal", db_sessionmaker)

    async def duration(path):
        return 60.0

    async def rate(path):
        return 25.0

    async def extract(path, output_dir):
        return str(tmp_path / "source_audio.wav")

    async def separate(audio, out, duration):
        return str(tmp_path / "vocals.wav"), str(tmp_path / "no_vocals.wav"), degraded
    monkeypatch.setattr(pipeline, "get_video_duration", duration)
    monkeypatch.setattr(pipeline, "video_frame_rate", rate)
    monkeypatch.setattr(pipeline, "extract_audio", extract)
    monkeypatch.setattr(pipeline, "separate_vocals_bgm", separate)
    analysed = []

    async def analysis(job_id, max_speakers=None):
        async with db_sessionmaker() as s:
            analysed.append((await s.execute(select(Job.status).where(Job.id == job_id))).scalar_one())
    monkeypatch.setattr(pipeline, "run_analysis_pipeline", analysis)
    async with db_sessionmaker() as s:
        project = Project(name="Ep")
        s.add(project)
        await s.flush()
        job = Job(project_id=project.id, video_path="v.mp4", status=JobStatus.PENDING)
        s.add(job)
        await s.commit()
        job_id = job.id
    return job_id, analysed


async def test_by_default_an_upload_waits_for_analyze(db_sessionmaker, monkeypatch, tmp_path):
    job_id, analysed = await _upload(db_sessionmaker, monkeypatch, tmp_path, "wait")
    await pipeline.run_pipeline(job_id)
    assert analysed == []
    async with db_sessionmaker() as s:
        assert (await s.execute(select(Job.status).where(Job.id == job_id))).scalar_one() == JobStatus.STEMS_READY


async def test_analyze_straight_away_claims_the_episode_and_analyses_it(db_sessionmaker, monkeypatch, tmp_path):
    job_id, analysed = await _upload(db_sessionmaker, monkeypatch, tmp_path, "analyze")
    await pipeline.run_pipeline(job_id)
    assert analysed == [JobStatus.DIARIZING]   # claimed as the Analyze button does


async def test_unseparated_music_still_waits_for_the_user(db_sessionmaker, monkeypatch, tmp_path):
    """Separation fell back to the original sound: the user may want Try again before analysing."""
    job_id, analysed = await _upload(db_sessionmaker, monkeypatch, tmp_path, "analyze", degraded=True)
    await pipeline.run_pipeline(job_id)
    assert analysed == []


async def test_a_click_first_means_no_second_run(db_sessionmaker, monkeypatch, tmp_path):
    job_id, analysed = await _upload(db_sessionmaker, monkeypatch, tmp_path, "analyze")
    real_update = pipeline._update_job

    async def clicked_meanwhile(db, job, status, progress, message=""):
        await real_update(db, job, status, progress, message)
        if status == JobStatus.STEMS_READY:   # the user clicks Analyze right as the stems are ready
            async with db_sessionmaker() as s:
                row = (await s.execute(select(Job).where(Job.id == job_id))).scalar_one()
                row.status = JobStatus.DIARIZING
                await s.commit()
    monkeypatch.setattr(pipeline, "_update_job", clicked_meanwhile)
    await pipeline.run_pipeline(job_id)
    assert analysed == []


async def test_the_progress_ring_knows_when_analysis_began(db_sessionmaker, monkeypatch, tmp_path):
    from app.services import job_clock
    job_id, analysed = await _upload(db_sessionmaker, monkeypatch, tmp_path, "analyze")
    await pipeline.run_pipeline(job_id)
    assert job_clock.elapsed(job_id, JobStatus.DIARIZING) is not None
