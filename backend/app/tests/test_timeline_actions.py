"""
Bulk timeline actions: auto-fit (speed clips to exactly fill their slots)
and tidy-lanes (repack scattered clips into minimal lanes).
"""
import pytest

from app.models.models import Segment
from app.tests.timeline_helpers import _add_segment, _make_job, _write_clip


@pytest.mark.asyncio
async def test_autofit_speeds_clips_to_their_slots(client, db_sessionmaker, tmp_path):
    job_id = await _make_job(client, db_sessionmaker)
    # 2s of audio in a 1s slot → needs 2.0×; 2s of audio in a 4s slot → slowed only to 0.9× (0.5× sounded wrong)
    long_id = await _add_segment(db_sessionmaker, job_id, 0.0, 1.0)
    short_id = await _add_segment(db_sessionmaker, job_id, 5.0, 9.0)
    async with db_sessionmaker() as session:
        for sid in (long_id, short_id):
            seg = await session.get(Segment, sid)
            seg.tts_audio_path = _write_clip(tmp_path, sid, 2.0)
        await session.commit()

    resp = await client.post(f"/api/v1/jobs/{job_id}/segments/autofit")
    assert resp.status_code == 200
    assert resp.json()["fitted"] == 2

    listed = {s["id"]: s for s in (await client.get(f"/api/v1/jobs/{job_id}/segments")).json()}
    assert listed[long_id]["voice_speed"] == pytest.approx(2.0)
    assert listed[long_id]["tts_duration_secs"] == pytest.approx(1.0, abs=0.15)
    assert listed[short_id]["voice_speed"] == pytest.approx(0.9)
    assert listed[short_id]["tts_duration_secs"] == pytest.approx(2.0 / 0.9, abs=0.15)


@pytest.mark.asyncio
async def test_autofit_skips_already_fitting_clips(client, db_sessionmaker, tmp_path):
    job_id = await _make_job(client, db_sessionmaker)
    seg_id = await _add_segment(db_sessionmaker, job_id, 0.0, 2.0)
    async with db_sessionmaker() as session:
        seg = await session.get(Segment, seg_id)
        seg.tts_audio_path = _write_clip(tmp_path, seg_id, 2.0)  # exactly fits
        await session.commit()

    resp = await client.post(f"/api/v1/jobs/{job_id}/segments/autofit")
    assert resp.status_code == 200
    assert resp.json() == {"fitted": 0, "skipped": 1, "missing": 0}


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
