"""
Batch TTS behavior: the background task must work through EVERY selected
segment on its own, retry failures, and never save silent mock audio as a
finished voice when real backends are configured.
"""
import asyncio

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


def _fake_synthesize(fail_first_call_for: set[str] | None = None, mock: bool = False):
    """A tts_client.synthesize stand-in that writes a real tiny wav."""
    calls: dict[str, int] = {}

    async def fake(text: str, output_path: str = "", **kwargs) -> dict:
        calls[text] = calls.get(text, 0) + 1
        if fail_first_call_for and text in fail_first_call_for and calls[text] == 1:
            return {"success": False, "audio_path": "", "duration_secs": 0, "error": "rate limited"}
        sf.write(output_path, np.zeros(2205, dtype=np.float32), 22050)
        result = {"success": True, "audio_path": output_path, "duration_secs": 0.1, "error": ""}
        if mock:
            result["mock"] = True
        return result

    fake.calls = calls
    return fake


async def _no_sleep(_secs):
    return None


@pytest.mark.asyncio
async def test_batch_synthesizes_every_segment(client, db_sessionmaker, monkeypatch, tmp_path):
    monkeypatch.setattr(tts_routes.settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(tts_routes.tts_client, "synthesize", _fake_synthesize())
    monkeypatch.setattr(tts_routes.asyncio, "sleep", _no_sleep)
    # The background task opens its own session — point it at the test DB.
    monkeypatch.setattr(core_db, "AsyncSessionLocal", db_sessionmaker)

    job_id, seg_ids = await _make_job_with_segments(client, db_sessionmaker, 3)
    resp = await client.post("/api/v1/tts/synthesize/batch", json={"segment_ids": seg_ids})
    assert resp.status_code == 202
    assert resp.json()["started"] == 3

    # ASGITransport runs the background task before returning control here.
    listed = (await client.get(f"/api/v1/jobs/{job_id}/segments")).json()
    assert all(s["tts_audio_path"] for s in listed), "every segment must get audio"


@pytest.mark.asyncio
async def test_batch_retries_failed_segments(client, db_sessionmaker, monkeypatch, tmp_path):
    monkeypatch.setattr(tts_routes.settings, "UPLOAD_DIR", str(tmp_path))
    fake = _fake_synthesize(fail_first_call_for={"ខ្មែរ 1"})
    monkeypatch.setattr(tts_routes.tts_client, "synthesize", fake)
    monkeypatch.setattr(tts_routes.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(core_db, "AsyncSessionLocal", db_sessionmaker)

    job_id, seg_ids = await _make_job_with_segments(client, db_sessionmaker, 3)
    resp = await client.post("/api/v1/tts/synthesize/batch", json={"segment_ids": seg_ids})
    assert resp.status_code == 202

    listed = (await client.get(f"/api/v1/jobs/{job_id}/segments")).json()
    assert all(s["tts_audio_path"] for s in listed), "failed segment must be retried to completion"
    assert fake.calls["ខ្មែរ 1"] == 2  # first attempt failed, retry pass succeeded


@pytest.mark.asyncio
async def test_mock_audio_rejected_when_real_backends_configured(client, db_sessionmaker, monkeypatch, tmp_path):
    monkeypatch.setattr(tts_routes.settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(tts_routes.settings, "GEMINI_API_KEY", "real-key")
    monkeypatch.setattr(tts_routes.tts_client, "synthesize", _fake_synthesize(mock=True))

    job_id, seg_ids = await _make_job_with_segments(client, db_sessionmaker, 1)
    resp = await client.post(f"/api/v1/tts/synthesize/segment/{seg_ids[0]}")
    assert resp.status_code == 503

    listed = (await client.get(f"/api/v1/jobs/{job_id}/segments")).json()
    assert listed[0]["tts_audio_path"] == "", "silent mock clip must not be saved as done"


@pytest.mark.asyncio
async def test_mock_audio_accepted_in_pure_mock_mode(client, db_sessionmaker, monkeypatch, tmp_path):
    """Pure mock mode = no TTS engine configured at all (the free HF Space counts as one)."""
    monkeypatch.setattr(tts_routes.settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(tts_routes.settings, "GEMINI_API_KEY", "")
    monkeypatch.setattr(tts_routes.settings, "VOXCPM2_API_URL", "")
    monkeypatch.setattr(tts_routes.settings, "VOXCPM2_HF_SPACE_FALLBACK", "")
    monkeypatch.setattr(tts_routes.tts_client, "synthesize", _fake_synthesize(mock=True))

    _, seg_ids = await _make_job_with_segments(client, db_sessionmaker, 1)
    resp = await client.post(f"/api/v1/tts/synthesize/segment/{seg_ids[0]}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_batch_status_reports_failures_and_backup_voices(client, db_sessionmaker, monkeypatch, tmp_path):
    """The editor learns when a batch is over, which lines failed and why, and which used Gemini's stock voice."""
    monkeypatch.setattr(tts_routes.settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(tts_routes.settings, "VOXCPM2_HF_SPACE_FALLBACK", "openbmb/VoxCPM-Demo")
    monkeypatch.setattr(tts_routes.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(core_db, "AsyncSessionLocal", db_sessionmaker)

    async def fake(text: str, output_path: str = "", **kwargs) -> dict:
        if text == "ខ្មែរ 0":
            return {"success": False, "audio_path": "", "duration_secs": 0, "error": "the voice server timed out"}
        sf.write(output_path, np.zeros(2205, dtype=np.float32), 22050)
        backend = "gemini" if text == "ខ្មែរ 1" else "voxcpm_hf_space"
        return {"success": True, "audio_path": output_path, "duration_secs": 0.1, "error": "", "backend": backend}

    monkeypatch.setattr(tts_routes.tts_client, "synthesize", fake)
    _job_id, seg_ids = await _make_job_with_segments(client, db_sessionmaker, 3)
    batch_id = (await client.post("/api/v1/tts/synthesize/batch", json={"segment_ids": seg_ids})).json()["batch_id"]

    status = (await client.get(f"/api/v1/tts/synthesize/batch/{batch_id}")).json()
    assert status["finished"] and status["total"] == 3 and status["done"] == 2
    assert status["backup_voice"] == 1
    assert status["failed"] == [{"segment_id": seg_ids[0], "reason": "the voice server timed out"}]
    assert (await client.get("/api/v1/tts/synthesize/batch/unknown")).status_code == 404


@pytest.mark.asyncio
async def test_a_single_line_says_when_the_backup_voice_was_used(client, db_sessionmaker, monkeypatch, tmp_path):
    monkeypatch.setattr(tts_routes.settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(tts_routes.settings, "VOXCPM2_HF_SPACE_FALLBACK", "openbmb/VoxCPM-Demo")

    async def fake(text: str, output_path: str = "", **kwargs) -> dict:
        sf.write(output_path, np.zeros(2205, dtype=np.float32), 22050)
        return {"success": True, "audio_path": output_path, "duration_secs": 0.1, "error": "", "backend": "gemini"}

    monkeypatch.setattr(tts_routes.tts_client, "synthesize", fake)
    _job_id, seg_ids = await _make_job_with_segments(client, db_sessionmaker, 1)
    body = (await client.post(f"/api/v1/tts/synthesize/segment/{seg_ids[0]}")).json()
    assert body["engine"] == "gemini" and body["backup_voice"] is True


@pytest.mark.asyncio
async def test_export_progress_is_saved_only_while_exporting(db_sessionmaker, monkeypatch):
    monkeypatch.setattr(core_db, "AsyncSessionLocal", db_sessionmaker)
    async with db_sessionmaker() as session:
        from app.models.models import Project
        project = Project(name="Ep")
        session.add(project)
        await session.flush()
        exporting = Job(project_id=project.id, status=JobStatus.MIXING, progress=5)
        done = Job(project_id=project.id, status=JobStatus.COMPLETED, progress=100)
        session.add_all([exporting, done])
        await session.commit()
        ids = exporting.id, done.id

    for job_id in ids:
        await tts_routes._set_mix_progress(job_id, 0.5)
    async with db_sessionmaker() as session:
        assert [(await session.get(Job, i)).progress for i in ids] == [50, 100]

    def unavailable():
        raise RuntimeError("database is unavailable")
    monkeypatch.setattr(core_db, "AsyncSessionLocal", unavailable)
    await tts_routes._set_mix_progress(ids[0], 0.6)      # logged, not raised
