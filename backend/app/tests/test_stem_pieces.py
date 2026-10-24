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
    body = (await client.get(f"/api/v1/jobs/{job_id}/stem-pieces")).json()
    assert (body["music"], body["voices"]) == ([], [])


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


# ── Task 2: the piece rules ───────────────────────────────────

from types import SimpleNamespace  # noqa: E402

from app.services.stem_pieces import MIN_PIECE_SECS, normalize, piece_gain_at, pieces_volume_expr  # noqa: E402

# (t, gain) for pieces [0,2] at −6 dB with a 0.5 s fade-out and [3,4] at 0 dB with a 0.5 s fade-in —
# the same table is in frontend/tests/stemPieces.test.ts
PIECE_TABLE = [(0.0, 0.5012), (1.0, 0.5012), (1.75, 0.2506), (2.0, 0.0), (2.5, 0.0), (3.0, 0.0), (3.25, 0.5), (3.9, 1.0)]


def _p(start, end, vol=0.0, fi=0.0, fo=0.0, on=False):
    return SimpleNamespace(start_time=start, end_time=end, volume_db=vol, fade_in_secs=fi, fade_out_secs=fo, in_export=on)


def test_pieces_are_kept_inside_their_neighbours():
    out = normalize("voices", [
        {"start_time": 5.0, "end_time": 8.0},
        {"start_time": -1.0, "end_time": 3.0, "volume_db": 40, "fade_in_secs": 3.0, "fade_out_secs": 3.0},
        {"start_time": 2.0, "end_time": 6.0},     # overlaps both: cut at the one before, never moved
        {"start_time": 19.0, "end_time": 25.0},
    ], duration=20.0)
    assert [(p["start_time"], p["end_time"]) for p in out] == [(0.0, 3.0), (3.0, 6.0), (6.0, 8.0), (19.0, 20.0)]
    assert out[0]["volume_db"] == 10.0
    assert out[0]["fade_in_secs"] + out[0]["fade_out_secs"] <= 3.0 + 1e-9


def test_a_piece_shorter_than_the_minimum_is_dropped():
    assert MIN_PIECE_SECS == 0.1
    out = normalize("music", [{"start_time": 1.0, "end_time": 1.05}, {"start_time": 4.0, "end_time": 3.0},
                              {"start_time": 2.0, "end_time": 3.0, "in_export": False}], duration=10.0)
    assert [(p["start_time"], p["end_time"], p["in_export"]) for p in out] == [(2.0, 3.0, True)]


def test_piece_gain_matches_the_shared_table():
    pieces = [_p(0.0, 2.0, vol=-6.0, fo=0.5), _p(3.0, 4.0, fi=0.5)]
    for t, g in PIECE_TABLE:
        assert piece_gain_at(pieces, t) == pytest.approx(g, abs=1e-4), t


def test_piece_expression_evaluates_like_piece_gain_at():
    pieces = [_p(0.0, 2.0, vol=-6.0, fo=0.5), _p(3.0, 4.0, fi=0.5, fo=0.25), _p(5.0, 7.0, vol=3.0, on=True)]
    expr = pieces_volume_expr(pieces, duration=10.0)
    env = {"gte": lambda a, b: float(a >= b), "lt": lambda a, b: float(a < b), "min": min, "__builtins__": {}}
    for i in range(0, 1000):
        t = i / 100
        assert eval(expr, {**env, "t": t}) == pytest.approx(piece_gain_at(pieces, t), abs=1e-4), t
    only = pieces_volume_expr(pieces, duration=10.0, export_only=True)
    assert eval(only, {**env, "t": 1.0}) == 0 and eval(only, {**env, "t": 6.0}) == pytest.approx(10 ** (3 / 20), abs=1e-4)


def test_untouched_pieces_need_no_expression():
    assert pieces_volume_expr([_p(0.0, 20.0)], duration=20.0) is None
    assert pieces_volume_expr([_p(0.0, 20.0, on=False)], duration=20.0, export_only=True) is None
    assert pieces_volume_expr([], duration=20.0) == "0"     # every piece deleted: silence
    assert pieces_volume_expr([_p(0.0, 20.0, vol=-2.0)], duration=20.0) is not None


@pytest.mark.asyncio
async def test_put_clamps_what_it_stores(client, db_sessionmaker):
    job_id = await _job_with_duration(client, db_sessionmaker, 10.0)
    pieces = (await client.put(f"/api/v1/jobs/{job_id}/stem-pieces/music", json={"pieces": [
        {"start_time": 8.0, "end_time": 12.0}, {"start_time": 0.0, "end_time": 9.0, "in_export": False}]})).json()
    assert [(p["start_time"], p["end_time"], p["in_export"]) for p in pieces] == [(0.0, 9.0, True), (9.0, 10.0, True)]


# ── Review fixes ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_stem_emptied_by_the_user_stays_silent(client, db_sessionmaker):
    job_id = await _job_with_duration(client, db_sessionmaker)
    first = (await client.get(f"/api/v1/jobs/{job_id}/stem-pieces")).json()
    assert first["made"] is True
    assert (await client.put(f"/api/v1/jobs/{job_id}/stem-pieces/music", json={"pieces": []})).json() == []
    assert (await client.put(f"/api/v1/jobs/{job_id}/stem-pieces/voices", json={"pieces": []})).json() == []
    again = (await client.get(f"/api/v1/jobs/{job_id}/stem-pieces")).json()
    assert (again["music"], again["voices"], again["made"]) == ([], [], True)


@pytest.mark.asyncio
async def test_an_episode_without_a_length_has_no_pieces_yet(client, db_sessionmaker):
    job_id = await _job_with_duration(client, db_sessionmaker, duration=0.0)
    assert (await client.get(f"/api/v1/jobs/{job_id}/stem-pieces")).json()["made"] is False
