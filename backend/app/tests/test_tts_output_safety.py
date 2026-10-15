"""
TTS output safety: a failed or fallen-back synthesis must never replace good
audio or be saved as a finished voice.

- Silent mock audio is only acceptable in pure mock mode (no TTS backend
  configured at all) — the free HF Space counts as a real backend.
- A failed regenerate leaves the segment's existing clips untouched (the raw
  clip is what later volume/filter/speed changes are re-applied from).
- The job-wide synthesis path follows the same rules as the per-segment one.
- A design voice is only baked from VoxCPM output, and a failed re-bake never
  leaves the voice cloning a reference that belongs to its old identity.
"""
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from sqlalchemy import select

import app.core.database as core_db
from app.api.routes import tts as tts_routes
from app.api.routes import voices as voices_routes
from app.models.models import Job, JobStatus, Segment, Voice


async def _job_with_segment(client, db_sessionmaker, *, approved=False, tts_path=""):
    project_id = (await client.post("/api/v1/projects/", json={"name": "Ep"})).json()["id"]
    async with db_sessionmaker() as s:
        job = Job(project_id=project_id, status=JobStatus.COMPLETED, progress=100)
        s.add(job)
        await s.flush()
        seg = Segment(job_id=job.id, start_time=0.0, end_time=1.5, source_text="你好",
                      khmer_text="សួស្តី", is_approved=approved, tts_audio_path=tts_path)
        s.add(seg)
        await s.commit()
        return project_id, job.id, seg.id


def _synth(*, mock=False, backend="voxcpm_gradio", success=True):
    calls = []

    async def fake(text: str, output_path: str = "", **kwargs) -> dict:
        calls.append(output_path)
        if not success:
            return {"success": False, "audio_path": "", "duration_secs": 0, "error": "down", "backend": backend}
        sf.write(output_path, np.zeros(2205, dtype=np.float32), 22050)
        out = {"success": True, "audio_path": output_path, "duration_secs": 0.1, "error": "", "backend": backend}
        if mock:
            out.update(mock=True, backend="mock")
        return out

    fake.calls = calls
    return fake


@pytest.fixture
def tts_env(monkeypatch, tmp_path):
    monkeypatch.setattr(tts_routes.settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(tts_routes.settings, "GEMINI_API_KEY", "")
    monkeypatch.setattr(tts_routes.settings, "VOXCPM2_API_URL", "")
    monkeypatch.setattr(tts_routes.settings, "VOXCPM2_HF_SPACE_FALLBACK", "openbmb/VoxCPM-Demo")
    return tmp_path


@pytest.mark.asyncio
async def test_mock_audio_rejected_when_only_the_free_hf_space_is_configured(client, db_sessionmaker, monkeypatch, tts_env):
    monkeypatch.setattr(tts_routes.tts_client, "synthesize", _synth(mock=True))
    job_id, seg_id = (await _job_with_segment(client, db_sessionmaker))[1:]
    resp = await client.post(f"/api/v1/tts/synthesize/segment/{seg_id}")
    assert resp.status_code == 503
    listed = (await client.get(f"/api/v1/jobs/{job_id}/segments")).json()
    assert listed[0]["tts_audio_path"] == ""


@pytest.mark.asyncio
async def test_failed_regenerate_keeps_the_existing_clips_intact(client, db_sessionmaker, monkeypatch, tts_env):
    project_id, job_id, seg_id = await _job_with_segment(client, db_sessionmaker)
    tts_dir = tts_env / project_id / job_id / "tts"
    tts_dir.mkdir(parents=True)
    raw, final = tts_dir / f"seg_{seg_id}_raw.wav", tts_dir / f"seg_{seg_id}.wav"
    raw.write_bytes(b"GOOD-RAW")
    final.write_bytes(b"GOOD-FINAL")

    monkeypatch.setattr(tts_routes.tts_client, "synthesize", _synth(mock=True))
    resp = await client.post(f"/api/v1/tts/synthesize/segment/{seg_id}")
    assert resp.status_code == 503
    assert raw.read_bytes() == b"GOOD-RAW", "a later effect tweak re-renders from this file"
    assert final.read_bytes() == b"GOOD-FINAL"
    assert sorted(p.name for p in tts_dir.iterdir()) == sorted([raw.name, final.name]), "no temp files left"


@pytest.mark.asyncio
async def test_job_synthesis_never_saves_mock_audio(client, db_sessionmaker, monkeypatch, tts_env):
    monkeypatch.setattr(tts_routes.tts_client, "synthesize", _synth(mock=True))
    monkeypatch.setattr(core_db, "AsyncSessionLocal", db_sessionmaker)
    job_id, seg_id = (await _job_with_segment(client, db_sessionmaker, approved=True))[1:]
    resp = await client.post(f"/api/v1/tts/synthesize/job/{job_id}")
    assert resp.status_code == 200
    listed = (await client.get(f"/api/v1/jobs/{job_id}/segments")).json()
    assert listed[0]["tts_audio_path"] == ""


@pytest.mark.asyncio
async def test_job_synthesis_saves_real_audio(client, db_sessionmaker, monkeypatch, tts_env):
    monkeypatch.setattr(tts_routes.tts_client, "synthesize", _synth())
    monkeypatch.setattr(core_db, "AsyncSessionLocal", db_sessionmaker)
    job_id, seg_id = (await _job_with_segment(client, db_sessionmaker, approved=True))[1:]
    await client.post(f"/api/v1/tts/synthesize/job/{job_id}")
    listed = (await client.get(f"/api/v1/jobs/{job_id}/segments")).json()
    assert listed[0]["tts_audio_path"].endswith(f"seg_{seg_id}.wav")
    assert Path(listed[0]["tts_audio_path"]).exists()


# ── Design-voice baking ───────────────────────────────────────────────────────

@pytest.fixture
def voices_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(voices_routes, "VOICES_DIR", tmp_path / "voices")
    return tmp_path / "voices"


@pytest.mark.asyncio
async def test_design_voice_is_not_baked_from_a_fallback_engine(client, monkeypatch, voices_dir):
    """A Gemini prebuilt voice as the clone reference would make every line sound like Gemini."""
    monkeypatch.setattr(voices_routes.tts_client, "synthesize", _synth(backend="gemini"))
    resp = await client.post("/api/v1/voices/", data={"name": "Hero", "mode": "design", "description": "deep male"})
    assert resp.status_code == 201
    assert resp.json()["reference_audio_path"] == ""


@pytest.mark.asyncio
async def test_design_voice_is_baked_from_voxcpm(client, monkeypatch, voices_dir):
    monkeypatch.setattr(voices_routes.tts_client, "synthesize", _synth(backend="voxcpm_hf_space"))
    resp = await client.post("/api/v1/voices/", data={"name": "Hero", "mode": "design", "description": "deep male"})
    ref = resp.json()["reference_audio_path"]
    assert ref.endswith("reference.wav") and Path(ref).exists()


@pytest.mark.asyncio
async def test_failed_rebake_stops_cloning_the_old_identity(client, db_sessionmaker, monkeypatch, voices_dir):
    monkeypatch.setattr(voices_routes.tts_client, "synthesize", _synth(backend="voxcpm_gradio"))
    voice = (await client.post("/api/v1/voices/", data={"name": "Hero", "mode": "design", "description": "deep"})).json()
    ref = Path(voice["reference_audio_path"])
    original = ref.read_bytes()

    monkeypatch.setattr(voices_routes.tts_client, "synthesize", _synth(mock=True))
    resp = await client.patch(f"/api/v1/voices/{voice['id']}", json={"description": "high-pitched child"})
    assert resp.status_code == 200
    assert resp.json()["reference_audio_path"] == "", "the old clip no longer matches this voice"
    assert ref.read_bytes() == original, "the failed bake must not overwrite the file"
