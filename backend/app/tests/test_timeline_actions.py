"""
Bulk timeline actions: Auto-fit (speed up voices that run into the next line,
never above 1.25×, never slowing one down) and tidy-lanes (repack scattered
clips into minimal lanes).
"""
import pytest

from app.models.models import Segment
from app.tests.timeline_helpers import _add_segment, _make_job, _write_clip


async def _voiced_clip(db_sessionmaker, job_id, tmp_path, start, tts, **fields) -> str:
    sid = await _add_segment(db_sessionmaker, job_id, start, start + tts, **fields)
    async with db_sessionmaker() as session:
        seg = await session.get(Segment, sid)
        seg.tts_audio_path = _write_clip(tmp_path, sid, tts)
        seg.tts_duration_secs = tts
        await session.commit()
    return sid


async def _get(db_sessionmaker, sid) -> Segment:
    async with db_sessionmaker() as session:
        return await session.get(Segment, sid)


@pytest.mark.asyncio
async def test_autofit_only_speeds_up_voices_that_run_into_the_next_line(client, db_sessionmaker, tmp_path):
    job_id = await _make_job(client, db_sessionmaker)
    a = await _voiced_clip(db_sessionmaker, job_id, tmp_path, 0.0, 2.4, source_start=0.0, source_end=2.0)
    await _add_segment(db_sessionmaker, job_id, 2.0, 3.0, lane=1, khmer_text="")       # B
    c = await _voiced_clip(db_sessionmaker, job_id, tmp_path, 10.0, 3.0)                # needs 1.5×
    await _add_segment(db_sessionmaker, job_id, 12.0, 12.5, lane=1, khmer_text="")
    d = await _voiced_clip(db_sessionmaker, job_id, tmp_path, 20.0, 1.0)                # nothing after it

    resp = await client.post(f"/api/v1/jobs/{job_id}/segments/autofit")
    assert resp.status_code == 200
    assert resp.json() == {"fitted": 2, "left": 1}

    sa, sc, sd = [await _get(db_sessionmaker, x) for x in (a, c, d)]
    assert sa.voice_speed == pytest.approx(1.2, abs=0.01)
    assert sa.end_time == pytest.approx(2.0, abs=0.02) and sa.start_time == 0.0
    assert sa.tts_duration_secs == pytest.approx(2.0, abs=0.03)
    assert sc.voice_speed == pytest.approx(1.25) and sc.end_time == pytest.approx(12.4, abs=0.03)
    assert (sd.voice_speed, sd.end_time) == (1.0, 21.0)


@pytest.mark.asyncio
async def test_autofit_never_slows_a_voice_down(client, db_sessionmaker, tmp_path):
    job_id = await _make_job(client, db_sessionmaker)
    short = await _voiced_clip(db_sessionmaker, job_id, tmp_path, 0.0, 1.0)            # far shorter than its slot
    await _add_segment(db_sessionmaker, job_id, 5.0, 6.0, lane=1, khmer_text="")
    fast = await _voiced_clip(db_sessionmaker, job_id, tmp_path, 10.0, 2.0, voice_speed=1.4)   # set by hand
    await _add_segment(db_sessionmaker, job_id, 11.0, 12.0, lane=1, khmer_text="")

    body = (await client.post(f"/api/v1/jobs/{job_id}/segments/autofit")).json()
    assert body == {"fitted": 0, "left": 1}
    s, f = await _get(db_sessionmaker, short), await _get(db_sessionmaker, fast)
    assert (s.voice_speed, s.end_time) == (1.0, 1.0)
    assert (f.voice_speed, f.end_time) == (1.4, 12.0)


@pytest.mark.asyncio
async def test_autofit_without_clips_404s(client, db_sessionmaker):
    job_id = await _make_job(client, db_sessionmaker)
    await _add_segment(db_sessionmaker, job_id, 0.0, 2.0)  # no audio
    assert (await client.post(f"/api/v1/jobs/{job_id}/segments/autofit")).status_code == 404


@pytest.mark.asyncio
async def test_tidy_lanes_packs_scattered_clips(client, db_sessionmaker):
    job_id = await _make_job(client, db_sessionmaker)
    # Clips scattered across lanes 2/1/3/5. Left-to-right greedy packing:
    # 0–1 → lane 0, 1–2 → lane 0 (touching is not overlapping),
    # 1.5–2.5 → lane 1 (true overlap with 1–2), 3–4 → lane 0.
    a = await _add_segment(db_sessionmaker, job_id, 0.0, 1.0, lane=2)
    b = await _add_segment(db_sessionmaker, job_id, 1.5, 2.5, lane=1)
    c = await _add_segment(db_sessionmaker, job_id, 3.0, 4.0, lane=3)
    d = await _add_segment(db_sessionmaker, job_id, 1.0, 2.0, lane=5)

    resp = await client.post(f"/api/v1/jobs/{job_id}/segments/tidy-lanes")
    assert resp.status_code == 200
    assert resp.json()["lanes"] == 2

    listed = (await client.get(f"/api/v1/jobs/{job_id}/segments")).json()
    lanes = {s["id"]: s["lane_index"] for s in listed}
    assert lanes == {a: 0, d: 0, b: 1, c: 0}
