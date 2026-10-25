"""Characters (0.5.0): the tables and columns for prints, checks and characters."""
import json

import pytest
import sqlalchemy
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import create_async_engine

import app.core.database as core_db
from app.models.models import Job, JobStatus, LinePrint, Project, Segment, Speaker
from app.schemas.schemas import SegmentResponse, SpeakerResponse
from app.services.pipeline_common import recover_orphaned_jobs
from app.tests.timeline_helpers import _add_segment, _make_job


async def _print(db_sessionmaker, segment_id: str, job_id: str) -> None:
    async with db_sessionmaker() as s:
        s.add(LinePrint(segment_id=segment_id, job_id=job_id, voice_print=b"\x00\x01", speaking_score=0.5,
                        model_version="t1"))
        await s.commit()


async def _prints(db_sessionmaker) -> list:
    async with db_sessionmaker() as s:
        return (await s.execute(select(LinePrint))).scalars().all()


@pytest.mark.asyncio
async def test_an_old_desktop_db_gets_the_new_columns(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/old.db")
    try:
        async with engine.begin() as conn:
            for table in ("segments", "speakers", "projects", "jobs"):
                await conn.execute(text(f"CREATE TABLE {table} (id VARCHAR PRIMARY KEY)"))
        await core_db.init_db(engine)
        async with engine.connect() as conn:
            def cols(table):
                return lambda c: {col["name"] for col in sqlalchemy.inspect(c).get_columns(table)}
            def tables(c):
                return set(sqlalchemy.inspect(c).get_table_names())
            assert {"who_check", "who_confirmed", "khmer_machine"} <= await conn.run_sync(cols("segments"))
            assert "face_crop_path" in await conn.run_sync(cols("speakers"))
            assert {"characters_version", "character_dismissals"} <= await conn.run_sync(cols("projects"))
            assert "line_prints" in await conn.run_sync(tables)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_line_prints_go_with_their_segment_job_and_project(client, db_sessionmaker):
    # a single line
    job_id = await _make_job(client, db_sessionmaker)
    seg_id = await _add_segment(db_sessionmaker, job_id, 0.0, 1.0)
    await _print(db_sessionmaker, seg_id, job_id)
    assert (await client.delete(f"/api/v1/segments/{seg_id}")).status_code in (200, 204)
    assert await _prints(db_sessionmaker) == []

    # a split keeps the first half's id: its print was the whole line's
    seg_id = await _add_segment(db_sessionmaker, job_id, 2.0, 6.0)
    await _print(db_sessionmaker, seg_id, job_id)
    assert (await client.post(f"/api/v1/segments/{seg_id}/split", json={"at": 4.0})).status_code == 200
    assert await _prints(db_sessionmaker) == []

    # a job
    other_job = await _make_job(client, db_sessionmaker)
    seg_id = await _add_segment(db_sessionmaker, other_job, 0.0, 1.0)
    await _print(db_sessionmaker, seg_id, other_job)
    assert (await client.delete(f"/api/v1/jobs/{other_job}")).status_code in (200, 204)
    assert await _prints(db_sessionmaker) == []

    # a project
    seg_id = await _add_segment(db_sessionmaker, job_id, 8.0, 9.0)
    await _print(db_sessionmaker, seg_id, job_id)
    async with db_sessionmaker() as s:
        project_id = (await s.get(Job, job_id)).project_id
    assert (await client.delete(f"/api/v1/projects/{project_id}")).status_code in (200, 204)
    assert await _prints(db_sessionmaker) == []


@pytest.mark.asyncio
async def test_replacing_a_jobs_segments_removes_their_prints(db_sessionmaker):
    from app.services.pipeline_common import replace_job_segments
    async with db_sessionmaker() as s:
        project = Project(name="P")
        s.add(project)
        await s.flush()
        job = Job(project_id=project.id, status=JobStatus.COMPLETED)
        s.add(job)
        await s.flush()
        seg = Segment(job_id=job.id, start_time=0.0, end_time=1.0)
        s.add(seg)
        await s.flush()
        s.add(LinePrint(segment_id=seg.id, job_id=job.id, model_version="t1"))
        await s.commit()
        job_id = job.id
    async with db_sessionmaker() as s:
        await replace_job_segments(s, job_id, [Segment(job_id=job_id, start_time=0.0, end_time=2.0)])
        await s.commit()
    assert await _prints(db_sessionmaker) == []


@pytest.mark.asyncio
async def test_segment_response_carries_who_check(client, db_sessionmaker):
    job_id = await _make_job(client, db_sessionmaker)
    seg_id = await _add_segment(db_sessionmaker, job_id, 0.0, 1.0)
    check = [{"speaker_id": "a", "basis": "voice"}, {"speaker_id": "b", "basis": "face"}]
    resp = await client.patch(f"/api/v1/segments/{seg_id}", json={"who_check": check, "who_confirmed": True})
    assert resp.status_code == 200
    assert resp.json()["who_check"] == check and resp.json()["who_confirmed"] is True
    async with db_sessionmaker() as s:
        assert json.loads((await s.get(Segment, seg_id)).who_check) == check   # stored as JSON text
    got = (await client.get(f"/api/v1/jobs/{job_id}/segments")).json()
    assert got[0]["who_check"] == check
    cleared = await client.patch(f"/api/v1/segments/{seg_id}", json={"who_check": None})
    assert cleared.json()["who_check"] is None


@pytest.mark.asyncio
async def test_a_null_who_confirmed_reads_as_false(client, db_sessionmaker):
    job_id = await _make_job(client, db_sessionmaker)
    seg_id = await _add_segment(db_sessionmaker, job_id, 0.0, 1.0)
    async with db_sessionmaker() as s:
        await s.execute(update(Segment).where(Segment.id == seg_id).values(who_confirmed=None, who_check="not json"))
        await s.commit()
    got = (await client.get(f"/api/v1/jobs/{job_id}/segments")).json()[0]
    assert got["who_confirmed"] is False and got["who_check"] is None


def test_speaker_response_face_crop_url():
    base = dict(id="s", project_id="p", label="SPEAKER_00", display_name="", gender="unknown",
                age_group="adult", voice_design_prompt="", reference_audio_path="", voice_id=None)
    assert SpeakerResponse(**base, face_crop_path=None).face_crop_url is None
    r = SpeakerResponse(**base, face_crop_path="/data/uploads/p/faces/a.jpg")
    assert r.face_crop_url == "/uploads/p/faces/a.jpg"


@pytest.mark.asyncio
async def test_a_job_left_recognizing_is_recovered(monkeypatch, db_sessionmaker):
    monkeypatch.setattr(core_db, "AsyncSessionLocal", db_sessionmaker)
    async with db_sessionmaker() as s:
        project = Project(name="P")
        s.add(project)
        await s.flush()
        with_lines = Job(project_id=project.id, status=JobStatus.RECOGNIZING)
        empty = Job(project_id=project.id, status=JobStatus.RECOGNIZING)
        s.add_all([with_lines, empty])
        await s.flush()
        s.add(Segment(job_id=with_lines.id, start_time=0.0, end_time=1.0, khmer_text="x"))
        await s.commit()
        ids = (with_lines.id, empty.id)
    await recover_orphaned_jobs()
    async with db_sessionmaker() as s:
        assert (await s.get(Job, ids[0])).status == JobStatus.COMPLETED
        assert (await s.get(Job, ids[1])).status == JobStatus.FAILED
