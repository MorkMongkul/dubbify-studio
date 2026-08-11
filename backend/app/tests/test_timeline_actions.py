"""
Bulk timeline actions: auto-fit (speed clips to exactly fill their slots)
and tidy-lanes (repack scattered clips into minimal lanes).
"""
import numpy as np
import pytest
import soundfile as sf

from app.models.models import Job, JobStatus, Segment


async def _make_job(client, db_sessionmaker) -> str:
    resp = await client.post("/api/v1/projects/", json={"name": "Ep"})
    project_id = resp.json()["id"]
    async with db_sessionmaker() as session:
        job = Job(project_id=project_id, status=JobStatus.COMPLETED, progress=100)
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


async def _add_segment(db_sessionmaker, job_id: str, start: float, end: float,
                       lane: int = 0, tts_path: str = "") -> str:
    async with db_sessionmaker() as session:
        seg = Segment(
            job_id=job_id, start_time=start, end_time=end, lane_index=lane,
            khmer_text="ខ្មែរ", tts_audio_path=tts_path,
            tts_duration_secs=1.0 if tts_path else None,
        )
        session.add(seg)
        await session.commit()
        await session.refresh(seg)
        return seg.id


def _write_clip(dir_path, seg_id: str, duration: float) -> str:
    """Write matching final + raw wavs; returns the final path."""
    final = dir_path / f"seg_{seg_id}.wav"
    raw = dir_path / f"seg_{seg_id}_raw.wav"
    samples = np.zeros(int(duration * 22050), dtype=np.float32)
    sf.write(str(raw), samples, 22050)
    sf.write(str(final), samples, 22050)
    return str(final)


@pytest.mark.asyncio
async def test_autofit_speeds_clips_to_their_slots(client, db_sessionmaker, tmp_path):
    job_id = await _make_job(client, db_sessionmaker)
    # 2s of audio in a 1s slot → needs 2.0×; 2s of audio in a 4s slot → 0.5×
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
    assert listed[short_id]["voice_speed"] == pytest.approx(0.5)
    assert listed[short_id]["tts_duration_secs"] == pytest.approx(4.0, abs=0.3)


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
