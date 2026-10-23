"""
Timeline 0.3.2 — the episode's own rows: the music & effects and the original
voices in pieces, locked to the picture (app/services/stem_pieces.py ↔
frontend/src/lib/stemPieces.ts). An episode starts with one whole piece per
stem, so older episodes sound exactly as before.
"""
import pytest

from app.models.models import Job
from app.tests.timeline_helpers import _make_job


async def _job_with_duration(client, db_sessionmaker, duration=20.0) -> str:
    job_id = await _make_job(client, db_sessionmaker)
    async with db_sessionmaker() as s:
        (await s.get(Job, job_id)).duration_secs = duration
        await s.commit()
    return job_id


# ── Task 1: the table and routes ──────────────────────────────

@pytest.mark.asyncio
async def test_an_episode_starts_with_one_whole_piece_per_stem(client, db_sessionmaker):
    job_id = await _job_with_duration(client, db_sessionmaker)
    body = (await client.get(f"/api/v1/jobs/{job_id}/stem-pieces")).json()
    (music,), (voices,) = body["music"], body["voices"]
    assert (music["start_time"], music["end_time"], music["volume_db"], music["in_export"]) == (0.0, 20.0, 0.0, True)
    assert (voices["start_time"], voices["end_time"], voices["in_export"]) == (0.0, 20.0, False)
    again = (await client.get(f"/api/v1/jobs/{job_id}/stem-pieces")).json()
    assert [p["id"] for p in again["music"]] == [music["id"]] and len(again["voices"]) == 1


@pytest.mark.asyncio
async def test_no_pieces_before_the_video_length_is_known(client, db_sessionmaker):
    job_id = await _job_with_duration(client, db_sessionmaker, duration=0.0)
    assert (await client.get(f"/api/v1/jobs/{job_id}/stem-pieces")).json() == {"music": [], "voices": []}


@pytest.mark.asyncio
async def test_put_replaces_a_stems_pieces(client, db_sessionmaker):
    job_id = await _job_with_duration(client, db_sessionmaker)
    resp = await client.put(f"/api/v1/jobs/{job_id}/stem-pieces/voices", json={"pieces": [
        {"start_time": 1.0, "end_time": 2.0, "in_export": True, "volume_db": -3.0},
        {"start_time": 5.0, "end_time": 6.5},
    ]})
    assert resp.status_code == 200
    pieces = resp.json()
    assert [(p["start_time"], p["end_time"], p["in_export"], p["volume_db"]) for p in pieces] == [
        (1.0, 2.0, True, -3.0), (5.0, 6.5, False, 0.0)]
    stored = (await client.get(f"/api/v1/jobs/{job_id}/stem-pieces")).json()
    assert len(stored["voices"]) == 2 and len(stored["music"]) == 1


@pytest.mark.asyncio
async def test_unknown_stem_or_job_is_refused(client, db_sessionmaker):
    job_id = await _job_with_duration(client, db_sessionmaker)
    assert (await client.put(f"/api/v1/jobs/{job_id}/stem-pieces/drums", json={"pieces": []})).status_code == 422
    assert (await client.get("/api/v1/jobs/nope/stem-pieces")).status_code == 404


@pytest.mark.asyncio
async def test_deleting_an_episode_or_project_removes_its_pieces(client, db_sessionmaker):
    from sqlalchemy import select
    from app.models.models import StemPiece
    job_id = await _job_with_duration(client, db_sessionmaker)
    await client.get(f"/api/v1/jobs/{job_id}/stem-pieces")
    assert (await client.delete(f"/api/v1/jobs/{job_id}")).status_code in (200, 204)
    other = await _job_with_duration(client, db_sessionmaker)
    await client.get(f"/api/v1/jobs/{other}/stem-pieces")
    async with db_sessionmaker() as s:
        project_id = (await s.get(Job, other)).project_id
    assert (await client.delete(f"/api/v1/projects/{project_id}")).status_code in (200, 204)
    async with db_sessionmaker() as s:
        assert (await s.execute(select(StemPiece))).scalars().all() == []
