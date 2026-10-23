"""
The 0.3.0 timeline rules: a clip with a voice is as long as its voice (as
trimmed, at its speed), a clip without one is as long as its text takes to
say, and clips never overlap on one track (app/services/timeline.py).
"""
import pytest
import sqlalchemy
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import app.core.database as core_db
from app.models.models import Job, JobStatus


async def _make_job(client, db_sessionmaker) -> str:
    resp = await client.post("/api/v1/projects/", json={"name": "Ep"})
    project_id = resp.json()["id"]
    async with db_sessionmaker() as session:
        job = Job(project_id=project_id, status=JobStatus.COMPLETED, progress=100)
        session.add(job)
        await session.commit()
        return job.id


# ── Task 1: columns ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_startup_adds_timeline_columns_to_existing_tables(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/old.db")
    try:
        async with engine.begin() as conn:
            await conn.execute(text(
                "CREATE TABLE segments (id VARCHAR PRIMARY KEY, job_id VARCHAR NOT NULL,"
                " start_time FLOAT NOT NULL, end_time FLOAT NOT NULL)"))
            await conn.execute(text(
                "CREATE TABLE jobs (id VARCHAR PRIMARY KEY, project_id VARCHAR NOT NULL)"))
        await core_db.init_db(engine)
        async with engine.connect() as conn:
            def cols(table):
                return lambda c: {col["name"] for col in sqlalchemy.inspect(c).get_columns(table)}
            segment_columns = await conn.run_sync(cols("segments"))
            job_columns = await conn.run_sync(cols("jobs"))
        assert {"source_start", "source_end", "voice_offset", "fit_rate", "voice_text"} <= segment_columns
        assert {"timeline_version", "frame_rate"} <= job_columns
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_segment_response_has_timing_fields(client, db_sessionmaker):
    job_id = await _make_job(client, db_sessionmaker)
    resp = await client.post(f"/api/v1/jobs/{job_id}/segments", json={"start_time": 1.0, "end_time": 2.0})
    assert resp.status_code == 201
    body = resp.json()
    assert (body["voice_offset"], body["fit_rate"], body["source_start"], body["voice_text"]) == (0.0, 1.0, None, None)
    job = (await client.get(f"/api/v1/jobs/{job_id}")).json()
    assert "frame_rate" in job


@pytest.mark.asyncio
async def test_timing_fields_read_from_a_pre_upgrade_row_have_defaults(client, db_sessionmaker):
    """add_missing_columns adds columns without a DEFAULT: old rows read NULL."""
    from app.models.models import Segment
    job_id = await _make_job(client, db_sessionmaker)
    async with db_sessionmaker() as session:
        seg = Segment(job_id=job_id, start_time=0.0, end_time=1.0)
        session.add(seg)
        await session.flush()
        seg.voice_offset = None
        seg.fit_rate = None
        await session.commit()
    body = (await client.get(f"/api/v1/jobs/{job_id}/segments")).json()[0]
    assert (body["voice_offset"], body["fit_rate"]) == (0.0, 1.0)
