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


# ── Task 2: the timing rules ──────────────────────────────────

import asyncio  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from sqlalchemy import select  # noqa: E402

from app.models.models import Segment  # noqa: E402
from app.services.timeline import (  # noqa: E402
    estimate_secs, free_lane, max_end, overlaps, overrun_target, rescale_for_speed, settle, settle_job,
    split_text, clamp_voiced, follow_text, actor_span, is_voiced,
)

_ids = iter(range(10_000))


def _seg(start, end, lane=0, tts=0.0, offset=None, fit=None, vs=1.0, source=None, text=""):
    return SimpleNamespace(
        id=f"s{next(_ids)}", start_time=start, end_time=end, lane_index=lane,
        tts_audio_path="/x.wav" if tts else "", tts_duration_secs=tts,
        voice_offset=offset, fit_rate=fit, voice_speed=vs,
        source_start=source[0] if source else None, source_end=source[1] if source else None,
        khmer_text=text,
    )


def test_estimate_follows_measured_pace():
    assert estimate_secs("ក" * 20) == 1.3
    assert estimate_secs("   ") == 0.1
    assert estimate_secs("") == 0.1


def test_max_end_counts_offset_and_rate():
    assert max_end(_seg(2.0, 3.0, tts=3.0, offset=0.5, fit=1.25)) == pytest.approx(4.0)
    assert max_end(_seg(1.0, 2.0, tts=2.0)) == pytest.approx(3.0)   # NULL offset/rate: 0 / 1


def test_clamp_voiced_keeps_the_clip_inside_its_voice():
    s = _seg(1.0, 9.0, tts=2.0)
    clamp_voiced(s)
    assert s.end_time == pytest.approx(3.0)
    s = _seg(1.0, 1.02, tts=2.0, offset=5.0)
    clamp_voiced(s)
    assert s.voice_offset == pytest.approx(1.9) and s.end_time == pytest.approx(1.1)
    s = _seg(1.0, 2.0, tts=2.0, offset=-1.0)
    clamp_voiced(s)
    assert s.voice_offset == 0.0


def test_follow_text_sizes_a_voiceless_line_only():
    s = _seg(2.0, 2.5, text="ក" * 30)
    assert follow_text(s) is True and s.end_time == pytest.approx(3.95)
    assert follow_text(s) is False
    blank = _seg(2.0, 2.5, text="  ")
    assert follow_text(blank) is False and blank.end_time == 2.5
    voiced = _seg(2.0, 2.5, tts=1.0, text="ក" * 30)
    assert follow_text(voiced) is False and voiced.end_time == 2.5


def test_actor_span_falls_back_to_the_clip():
    assert actor_span(_seg(1.0, 2.0, source=(0.5, 1.5))) == (0.5, 1.5)
    assert actor_span(_seg(1.0, 2.0)) == (1.0, 2.0)


def test_is_voiced_needs_a_file_and_a_length():
    assert is_voiced(_seg(0, 1, tts=1.0))
    assert not is_voiced(_seg(0, 1))


def test_free_lane_prefers_nearest_then_below_then_new():
    a, b, c = _seg(0, 5, lane=0), _seg(0, 5, lane=1), _seg(0, 5, lane=2)
    blocker = _seg(10, 12, lane=1)
    moving = _seg(11, 13, lane=1)
    assert free_lane([blocker, moving, _seg(0, 1, lane=0), _seg(0, 1, lane=2)], moving) == 2
    assert free_lane([blocker, moving, _seg(10, 12, lane=0), _seg(10, 12, lane=2)], moving) == 3
    assert free_lane([blocker, moving, _seg(10, 12, lane=2), _seg(0, 1, lane=0)], moving) == 0
    assert free_lane([a, b, c, _seg(20, 21, lane=1)], _seg(20.5, 22, lane=1)) == 2
    lone = _seg(0, 1, lane=4)
    assert free_lane([lone], lone) == 4


def test_settle_moves_only_the_overlapping_clip_never_in_time():
    a = _seg(0.0, 2.0, lane=0)
    b = _seg(1.5, 3.0, lane=0)
    moved = settle([a, b], [b.id])
    assert moved == {b.id}
    assert b.lane_index == 1 and (b.start_time, b.end_time) == (1.5, 3.0)
    assert a.lane_index == 0 and (a.start_time, a.end_time) == (0.0, 2.0)
    assert settle([a, b], [a.id, b.id]) == set()


def test_overlaps_ignores_touching_clips():
    assert not overlaps(_seg(0, 1), _seg(1, 2))
    assert overlaps(_seg(0, 1.1), _seg(1, 2))


def test_overrun_target_ignores_overlaps_the_original_had():
    a = _seg(0.0, 3.8, tts=3.8, source=(0.0, 3.4))
    b = _seg(3.0, 5.0)
    assert overrun_target(a, [a, b]) == pytest.approx(3.4)
    a.end_time = 3.3
    assert overrun_target(a, [a, b]) is None
    c = _seg(0.0, 3.8, tts=3.8)
    assert overrun_target(c, [c, _seg(3.0, 5.0, lane=2)]) == pytest.approx(3.0)


def test_rescale_keeps_the_same_words_cut():
    s = _seg(0.0, 2.0, tts=3.0, offset=0.5, fit=1.25, vs=1.0)
    rescale_for_speed(s, 1.0)
    assert (s.voice_offset, s.end_time, s.voice_speed, s.fit_rate) == pytest.approx((0.5, 2.5, 1.0, 1.0))
    rescale_for_speed(s, 1.25)
    assert (s.voice_offset, s.end_time) == pytest.approx((0.4, 2.0))


def test_split_text_at_nearest_phrase_break():
    assert split_text("aaaa bbbb cccc", 0.6) == ("aaaa bbbb", "cccc")
    assert split_text("aaaa bbbb cccc", 1 / 3) == ("aaaa", "bbbb cccc")
    assert split_text("សួស្តី។ ទៅណា?", 0.5) == ("សួស្តី។", "ទៅណា?")


def test_split_text_without_a_break_keeps_both_halves_whole():
    assert split_text("abc", 0.5) == ("abc", "abc")
    assert split_text("", 0.5) == ("", "")
    assert split_text("abc ", 0.5) == ("abc ", "abc ")


@pytest.mark.asyncio
async def test_settle_job_never_leaves_overlaps_when_run_concurrently(client, db_sessionmaker):
    job_id = await _make_job(client, db_sessionmaker)
    async with db_sessionmaker() as session:
        segs = [Segment(job_id=job_id, start_time=i * 2.0, end_time=i * 2.0 + 1.5, lane_index=0)
                for i in range(6)]
        session.add_all(segs)
        await session.commit()
        ids = [s.id for s in segs]

    async def grow(seg_id: str):
        async with db_sessionmaker() as session:
            seg = await session.get(Segment, seg_id)
            seg.end_time = seg.start_time + 3.0
            await session.commit()
            await asyncio.sleep(0)
            await settle_job(session, job_id, [seg_id])

    await asyncio.gather(*(grow(i) for i in ids))

    async with db_sessionmaker() as session:
        rows = (await session.execute(select(Segment).where(Segment.job_id == job_id))).scalars().all()
    for i, a in enumerate(rows):
        assert a.end_time == pytest.approx(a.start_time + 3.0)
        for b in rows[i + 1:]:
            if a.lane_index == b.lane_index:
                assert not overlaps(a, b), (a.start_time, b.start_time, a.lane_index)


# ── Task 12: the video's frame rate (one ← / → step) ─────────

def test_frame_rate_is_read_from_the_video():
    from app.services.audio_extractor import frame_rate_of
    assert frame_rate_of({"streams": [{"codec_type": "audio"}, {"codec_type": "video", "r_frame_rate": "30000/1001"}]}) \
        == pytest.approx(29.97, abs=0.01)
    assert frame_rate_of({"streams": [{"codec_type": "video", "avg_frame_rate": "25/1", "r_frame_rate": "0/0"}]}) == 25.0
    assert frame_rate_of({"streams": [{"codec_type": "audio"}]}) is None
    assert frame_rate_of({}) is None
    assert frame_rate_of({"streams": [{"codec_type": "video", "r_frame_rate": "nonsense"}]}) is None


@pytest.mark.asyncio
async def test_opening_an_episode_fills_a_missing_frame_rate_once(client, db_sessionmaker, monkeypatch, tmp_path):
    from app.api.routes import jobs as jobs_routes
    job_id = await _make_job(client, db_sessionmaker)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    async with db_sessionmaker() as s:
        (await s.get(Job, job_id)).video_path = str(video)
        await s.commit()
    calls = []

    async def fake_probe(path):
        calls.append(path)
        return {"streams": [{"codec_type": "video", "r_frame_rate": "24/1"}]}

    monkeypatch.setattr(jobs_routes, "probe_video", fake_probe)
    assert (await client.get(f"/api/v1/jobs/{job_id}")).json()["frame_rate"] == 24.0
    assert (await client.get(f"/api/v1/jobs/{job_id}")).json()["frame_rate"] == 24.0
    assert len(calls) == 1

    async def broken_probe(path):
        raise RuntimeError("ffprobe failed")

    other = await _make_job(client, db_sessionmaker)
    monkeypatch.setattr(jobs_routes, "probe_video", broken_probe)
    resp = await client.get(f"/api/v1/jobs/{other}")
    assert resp.status_code == 200 and resp.json()["frame_rate"] is None
