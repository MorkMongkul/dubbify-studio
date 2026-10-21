"""
The editor's progress ring estimates how far the running step has got from how
long that step has been running, which GET /jobs/{id} reports as
step_elapsed_secs.
"""
import asyncio

import pytest

from app.models.models import Job, JobStatus
from app.services import job_clock


async def _job(client, db_sessionmaker, status: JobStatus) -> str:
    """An episode whose step `status` starts now, the way the pipeline and the routes start one."""
    project_id = (await client.post("/api/v1/projects/", json={"name": "Ep"})).json()["id"]
    async with db_sessionmaker() as session:
        job = Job(project_id=project_id, status=JobStatus.STEMS_READY, progress=30)
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job.status = status
        await session.commit()
        return job.id


async def _set_status(db_sessionmaker, job_id: str, status: JobStatus) -> None:
    async with db_sessionmaker() as session:
        job = await session.get(Job, job_id)
        job.status = status
        await session.commit()


async def _elapsed(client, job_id: str):
    return (await client.get(f"/api/v1/jobs/{job_id}")).json()["step_elapsed_secs"]


@pytest.mark.asyncio
async def test_a_running_step_reports_how_long_it_has_run(client, db_sessionmaker):
    job_id = await _job(client, db_sessionmaker, JobStatus.DIARIZING)
    await asyncio.sleep(0.25)
    first = await _elapsed(client, job_id)
    assert first is not None and first >= 0.2
    await asyncio.sleep(0.25)
    assert await _elapsed(client, job_id) > first


@pytest.mark.asyncio
async def test_the_clock_restarts_when_the_next_step_begins(client, db_sessionmaker):
    job_id = await _job(client, db_sessionmaker, JobStatus.DIARIZING)
    await asyncio.sleep(0.4)
    await _set_status(db_sessionmaker, job_id, JobStatus.TRANSLATING)
    assert await _elapsed(client, job_id) < 0.3


@pytest.mark.asyncio
async def test_setting_the_same_step_again_keeps_its_clock(client, db_sessionmaker):
    # update_job_status re-sets the status a route already set (e.g. TRANSLATING)
    job_id = await _job(client, db_sessionmaker, JobStatus.TRANSLATING)
    await asyncio.sleep(0.4)
    await _set_status(db_sessionmaker, job_id, JobStatus.TRANSLATING)
    assert await _elapsed(client, job_id) >= 0.3


@pytest.mark.asyncio
async def test_unknown_for_a_step_that_did_not_start_in_this_run(client, db_sessionmaker):
    # e.g. an episode created already finished, or one loaded after a restart
    project_id = (await client.post("/api/v1/projects/", json={"name": "Ep"})).json()["id"]
    async with db_sessionmaker() as session:
        job = Job(project_id=project_id, status=JobStatus.COMPLETED, progress=100)
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id
    assert await _elapsed(client, job_id) is None


@pytest.mark.asyncio
async def test_the_ending_step_keeps_its_time_until_the_next_one_is_saved(client, db_sessionmaker):
    # update_job_status sets the next step and then commits: a poll in between
    # still reads the step that's ending, and must get its time, not nothing
    job_id = await _job(client, db_sessionmaker, JobStatus.DIARIZING)
    await asyncio.sleep(0.3)
    async with db_sessionmaker() as session:
        job = await session.get(Job, job_id)
        job.status = JobStatus.TRANSCRIBING          # set, not saved yet
        assert job_clock.elapsed(job_id, JobStatus.DIARIZING) >= 0.3
        assert job_clock.elapsed(job_id, JobStatus.TRANSCRIBING) < 0.1
        await session.commit()

