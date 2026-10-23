"""
Timeline 0.3.1 — shaping the sound: fades on voice clips, a volume line,
music that dips under voices (ducking). The preview and the export apply the
same numbers (app/services/timeline.py ↔ frontend/src/lib/clipRules.ts,
ducking.ts). Older episodes keep sounding as they did: no fades, no ducking.
"""
import pytest
import sqlalchemy
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import app.core.database as core_db
from app.models.models import Job, Segment
from app.tests.timeline_helpers import _add_segment, _make_job


# ── Task 1: columns ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_startup_adds_fade_and_ducking_columns(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/old.db")
    try:
        async with engine.begin() as conn:
            await conn.execute(text(
                "CREATE TABLE segments (id VARCHAR PRIMARY KEY, job_id VARCHAR NOT NULL,"
                " start_time FLOAT NOT NULL, end_time FLOAT NOT NULL)"))
            await conn.execute(text("CREATE TABLE jobs (id VARCHAR PRIMARY KEY, project_id VARCHAR NOT NULL)"))
        await core_db.init_db(engine)
        async with engine.connect() as conn:
            def cols(table):
                return lambda c: {col["name"] for col in sqlalchemy.inspect(c).get_columns(table)}
            segment_columns = await conn.run_sync(cols("segments"))
            job_columns = await conn.run_sync(cols("jobs"))
        assert {"fade_in_secs", "fade_out_secs"} <= segment_columns
        assert "duck_music" in job_columns
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_segment_response_has_fades(client, db_sessionmaker):
    job_id = await _make_job(client, db_sessionmaker)
    body = (await client.post(f"/api/v1/jobs/{job_id}/segments", json={"start_time": 1.0, "end_time": 2.0})).json()
    assert (body["fade_in_secs"], body["fade_out_secs"]) == (0.0, 0.0)
    sid = await _add_segment(db_sessionmaker, job_id, 3.0, 4.0)
    async with db_sessionmaker() as s:
        seg = await s.get(Segment, sid)
        seg.fade_in_secs = seg.fade_out_secs = None     # a row from before 0.3.1
        await s.commit()
    row = [x for x in (await client.get(f"/api/v1/jobs/{job_id}/segments")).json() if x["id"] == sid][0]
    assert (row["fade_in_secs"], row["fade_out_secs"]) == (0.0, 0.0)


@pytest.mark.asyncio
async def test_ducking_is_off_for_an_older_episode_and_can_be_switched(client, db_sessionmaker):
    job_id = await _make_job(client, db_sessionmaker)
    async with db_sessionmaker() as s:
        (await s.get(Job, job_id)).duck_music = None
        await s.commit()
    assert (await client.get(f"/api/v1/jobs/{job_id}")).json()["duck_music"] is False
    resp = await client.patch(f"/api/v1/jobs/{job_id}/ducking", json={"duck_music": True})
    assert resp.status_code == 200 and resp.json()["duck_music"] is True
    assert (await client.patch("/api/v1/jobs/nope/ducking", json={"duck_music": True})).status_code == 404
