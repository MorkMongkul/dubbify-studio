"""
Regression tests for batch TTS concurrency + adaptive Gemini pacing.

Bug: /synthesize/batch and /synthesize/job/{id} looped through segments one
at a time with a blanket 1.5s sleep between every one — even when VoxCPM2
(not Gemini) was the backend actually being used, where that sleep protects
nothing. tts_client.py also had a synthesize_batch() with real concurrency
that nothing ever called. These tests prove the batch endpoints now (a)
actually overlap segment synthesis instead of fully serializing, and (b)
only pace requests once a Gemini fallback is actually observed.
"""
import asyncio
import time

import numpy as np
import pytest
import soundfile as sf

import app.core.database as core_db
from app.api.routes import tts as tts_routes
from app.models.models import Job, JobStatus, Segment


async def _make_job_with_segments(client, db_sessionmaker, n: int) -> tuple[str, list[str]]:
    resp = await client.post("/api/v1/projects/", json={"name": "Ep"})
    project_id = resp.json()["id"]
    async with db_sessionmaker() as session:
        job = Job(project_id=project_id, status=JobStatus.COMPLETED, progress=100)
        session.add(job)
        await session.flush()
        seg_ids = []
        for i in range(n):
            seg = Segment(
                job_id=job.id, start_time=i * 2.0, end_time=i * 2.0 + 1.5,
                source_text=f"line {i}", khmer_text=f"ខ្មែរ {i}",
            )
            session.add(seg)
            await session.flush()
            seg_ids.append(seg.id)
        job_id = job.id
        await session.commit()
    return job_id, seg_ids


def _slow_synthesize(delay: float, backend: str = "voxcpm_hf_space"):
    """Every call takes `delay` seconds — simulates a real TTS network call
    so wall-clock time reveals whether calls actually overlapped."""

    async def fake(text: str, output_path: str = "", **kwargs) -> dict:
        await asyncio.sleep(delay)
        sf.write(output_path, np.zeros(2205, dtype=np.float32), 22050)
        return {
            "success": True, "audio_path": output_path, "duration_secs": 0.1,
            "error": "", "backend": backend,
        }

    return fake


@pytest.mark.asyncio
async def test_batch_segments_synthesize_concurrently(client, db_sessionmaker, monkeypatch, tmp_path):
    """4 segments at 0.2s each, concurrency=2, must take ~0.4s total — not ~0.8s.
    (Real asyncio.sleep is used deliberately here, NOT the no-sleep stand-in
    other tests use, since wall-clock time is exactly what's under test.)"""
    monkeypatch.setattr(tts_routes.settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(tts_routes.settings, "TTS_BATCH_CONCURRENCY", 2)
    monkeypatch.setattr(tts_routes.tts_client, "synthesize", _slow_synthesize(0.2))
    monkeypatch.setattr(core_db, "AsyncSessionLocal", db_sessionmaker)

    job_id, seg_ids = await _make_job_with_segments(client, db_sessionmaker, 4)

    start = time.monotonic()
    resp = await client.post("/api/v1/tts/synthesize/batch", json={"segment_ids": seg_ids})
    elapsed = time.monotonic() - start

    assert resp.status_code == 202
    listed = (await client.get(f"/api/v1/jobs/{job_id}/segments")).json()
    assert all(s["tts_audio_path"] for s in listed)

    # Fully sequential would be >= 4 * 0.2 = 0.8s. Concurrency=2 should land
    # close to 2 * 0.2 = 0.4s. Generous upper bound to avoid CI flakiness.
    assert elapsed < 0.7, f"segments did not overlap — took {elapsed:.2f}s (sequential would be ~0.8s)"


@pytest.mark.asyncio
async def test_no_pacing_delay_when_voxcpm_backend_used(client, db_sessionmaker, monkeypatch, tmp_path):
    """VoxCPM2 has no request-rate limit — the batch task must not insert
    artificial sleeps between segments that never touch Gemini."""
    monkeypatch.setattr(tts_routes.settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(tts_routes.settings, "TTS_BATCH_CONCURRENCY", 3)
    monkeypatch.setattr(tts_routes.tts_client, "synthesize", _slow_synthesize(0.0, backend="voxcpm_hf_space"))
    monkeypatch.setattr(core_db, "AsyncSessionLocal", db_sessionmaker)

    job_id, seg_ids = await _make_job_with_segments(client, db_sessionmaker, 5)

    start = time.monotonic()
    resp = await client.post("/api/v1/tts/synthesize/batch", json={"segment_ids": seg_ids})
    elapsed = time.monotonic() - start

    assert resp.status_code == 202
    # No Gemini involved anywhere — should finish near-instantly, not
    # 5 * 1.5s = 7.5s the old blanket pacing would have cost.
    assert elapsed < 1.0, f"unexpected pacing delay with no Gemini fallback — took {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_pacing_kicks_in_only_after_gemini_fallback_observed(client, db_sessionmaker, monkeypatch, tmp_path):
    """Once a segment actually falls back to Gemini, subsequent requests in
    this batch should slow down to respect Gemini's rate limit."""
    monkeypatch.setattr(tts_routes.settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(tts_routes.settings, "TTS_BATCH_CONCURRENCY", 1)  # deterministic ordering
    monkeypatch.setattr(tts_routes.tts_client, "synthesize", _slow_synthesize(0.0, backend="gemini"))
    monkeypatch.setattr(core_db, "AsyncSessionLocal", db_sessionmaker)

    job_id, seg_ids = await _make_job_with_segments(client, db_sessionmaker, 3)

    start = time.monotonic()
    resp = await client.post("/api/v1/tts/synthesize/batch", json={"segment_ids": seg_ids})
    elapsed = time.monotonic() - start

    assert resp.status_code == 202
    # First segment sets gemini_seen; the next 2 each pay the 1.5s pacing
    # delay before their call -> at least ~3.0s.
    assert elapsed >= 2.9, f"expected Gemini pacing after fallback observed — took only {elapsed:.2f}s"
