"""
Voices and export for single-voice dub (0.5.7): every line with Khmer and no current voice is voiced by the project's
Edge voice, then the episode is exported. A line Edge couldn't voice means no export (never a video with holes). A
line's own Generate Voice in such a project uses Edge too; a normal project's never does.
"""
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.models import Job, JobStatus, Project, Segment
from app.services.single_voice import voices

pytestmark = pytest.mark.asyncio


async def _project(db_sessionmaker, monkeypatch, tmp_path, dub_mode="single_voice"):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr("app.core.database.AsyncSessionLocal", db_sessionmaker)
    async with db_sessionmaker() as s:
        project = Project(name="Quick", dub_mode=dub_mode, voice_name="km-KH-SreymomNeural", voice_rate=10)
        s.add(project)
        await s.flush()
        job = Job(project_id=project.id, status=JobStatus.COMPLETED, video_path="v.mp4", duration_secs=20.0)
        s.add(job)
        await s.flush()
        lines = [
            Segment(job_id=job.id, start_time=0, end_time=1, source_text="一", khmer_text="មួយ"),
            Segment(job_id=job.id, start_time=2, end_time=3, source_text="二", khmer_text="ពីរ",
                    tts_audio_path="/v/2.wav", voice_text="ពីរ"),                       # voiced already
            Segment(job_id=job.id, start_time=4, end_time=5, source_text="三", khmer_text="បីថ្មី",
                    tts_audio_path="/v/3.wav", voice_text="បី"),                         # text changed since
            Segment(job_id=job.id, start_time=6, end_time=7, source_text="四", khmer_text=""),   # nothing to say
        ]
        s.add_all(lines)
        await s.commit()
        return job.id, [l.id for l in lines]


def _voicing(monkeypatch, fail=()):
    voiced = []

    async def synth(segment_id, db):
        voiced.append(segment_id)
        if segment_id in fail:
            raise RuntimeError("Edge's voice service didn't answer")
        return None, "edge"
    monkeypatch.setattr(voices.tts_routes, "_synthesize_segment_db", synth)
    mixed = []

    async def mix(job_id, mute_original, export_path=None):
        mixed.append((job_id, mute_original, export_path))
    monkeypatch.setattr(voices.tts_routes, "_mix_job_task", mix)
    return voiced, mixed


async def test_lines_without_a_current_voice_are_voiced_then_exported(db_sessionmaker, monkeypatch, tmp_path):
    job_id, ids = await _project(db_sessionmaker, monkeypatch, tmp_path)
    kept = tmp_path / "2.wav"
    kept.write_bytes(b"RIFF")
    async with db_sessionmaker() as s:   # line 2's voice is really there
        seg = (await s.execute(select(Segment).where(Segment.id == ids[1]))).scalar_one()
        seg.tts_audio_path = str(kept)
        await s.commit()
    voiced, mixed = _voicing(monkeypatch)
    await voices.voice_and_export(job_id, "/Users/x/Movies/out.mp4")
    assert sorted(voiced) == sorted([ids[0], ids[2]])
    assert mixed == [(job_id, True, "/Users/x/Movies/out.mp4")]


async def test_a_line_edge_couldnt_voice_means_no_export(db_sessionmaker, monkeypatch, tmp_path):
    job_id, ids = await _project(db_sessionmaker, monkeypatch, tmp_path)
    voiced, mixed = _voicing(monkeypatch, fail={ids[2]})
    await voices.voice_and_export(job_id, None)
    assert mixed == []
    async with db_sessionmaker() as s:
        job = (await s.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert job.status == JobStatus.COMPLETED and "1 line has no voice" in job.error_msg


async def test_the_export_route_claims_the_episode(client, db_sessionmaker, monkeypatch, tmp_path):
    job_id, _ = await _project(db_sessionmaker, monkeypatch, tmp_path)
    started = []

    async def run(job_id, export_path):
        started.append((job_id, export_path))
    monkeypatch.setattr("app.api.routes.single_voice.voices.voice_and_export", run)
    resp = await client.post(f"/api/v1/single-voice/jobs/{job_id}/export", json={})
    assert resp.status_code == 202 and started == [(job_id, None)]
    async with db_sessionmaker() as s:
        assert (await s.execute(select(Job.status).where(Job.id == job_id))).scalar_one() == JobStatus.SYNTHESIZING
    assert (await client.post(f"/api/v1/single-voice/jobs/{job_id}/export", json={})).status_code == 409


async def test_the_export_route_is_only_for_single_voice_projects(client, db_sessionmaker, monkeypatch, tmp_path):
    job_id, _ = await _project(db_sessionmaker, monkeypatch, tmp_path, dub_mode="characters")
    assert (await client.post(f"/api/v1/single-voice/jobs/{job_id}/export", json={})).status_code == 400


# ── a line's own Generate Voice ──────────────────────────────────

async def _one_line_voice(client, db_sessionmaker, monkeypatch, tmp_path, dub_mode):
    job_id, ids = await _project(db_sessionmaker, monkeypatch, tmp_path, dub_mode=dub_mode)
    from app.api.routes import tts as tts_routes
    from app.services.single_voice import edge_voice
    used = []

    async def edge(text, output_path, voice, rate):
        used.append(("edge", voice, rate))
        from pathlib import Path
        import subprocess
        from app.core.binaries import ffmpeg
        subprocess.run([ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=220:d=0.6", output_path],
                       check=True)
        return {"success": True, "audio_path": output_path, "duration_secs": 0.6, "error": "", "backend": "edge"}

    async def other(**kwargs):
        used.append(("tts_client",))
        return {"success": False, "error": "not this one"}

    async def no_voice(seg, db):
        return {}
    monkeypatch.setattr(edge_voice, "synthesize", edge)
    monkeypatch.setattr(tts_routes.tts_client, "synthesize", other)
    monkeypatch.setattr(tts_routes, "_resolve_voice", no_voice)
    await client.post(f"/api/v1/tts/synthesize/segment/{ids[0]}")
    return used


async def test_a_lines_own_voice_uses_edge_in_a_single_voice_project(client, db_sessionmaker, monkeypatch, tmp_path):
    used = await _one_line_voice(client, db_sessionmaker, monkeypatch, tmp_path, "single_voice")
    assert used == [("edge", "km-KH-SreymomNeural", 10)]


async def test_a_normal_projects_line_never_uses_edge(client, db_sessionmaker, monkeypatch, tmp_path):
    used = await _one_line_voice(client, db_sessionmaker, monkeypatch, tmp_path, "characters")
    assert used == [("tts_client",)]


# ── review fixes ─────────────────────────────────────────────────

async def test_warnings_from_the_analysis_survive_a_clean_export(db_sessionmaker, monkeypatch, tmp_path):
    job_id, ids = await _project(db_sessionmaker, monkeypatch, tmp_path)
    async with db_sessionmaker() as s:
        job = (await s.execute(select(Job).where(Job.id == job_id))).scalar_one()
        job.error_msg = "2 line(s) were translated by the basic fallback"
        await s.commit()
    voiced, mixed = _voicing(monkeypatch)

    async def clean_mix(job_id, mute_original, export_path=None):
        async with db_sessionmaker() as s:   # what _mix_job_task does when it succeeds
            job = (await s.execute(select(Job).where(Job.id == job_id))).scalar_one()
            job.status, job.error_msg = JobStatus.COMPLETED, ""
            await s.commit()
    monkeypatch.setattr(voices.tts_routes, "_mix_job_task", clean_mix)
    await voices.voice_and_export(job_id, None)
    async with db_sessionmaker() as s:
        job = (await s.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert "basic fallback" in job.error_msg and "1 line had no Khmer" in job.error_msg


async def test_progress_moves_as_voices_are_made(db_sessionmaker, monkeypatch, tmp_path):
    job_id, ids = await _project(db_sessionmaker, monkeypatch, tmp_path)
    seen = []

    async def synth(segment_id, db):
        async with db_sessionmaker() as s:
            seen.append((await s.execute(select(Job.progress).where(Job.id == job_id))).scalar_one())
        return None, "edge"
    monkeypatch.setattr(voices.tts_routes, "_synthesize_segment_db", synth)

    async def mix(job_id, mute_original, export_path=None):
        pass
    monkeypatch.setattr(voices.tts_routes, "_mix_job_task", mix)
    monkeypatch.setattr(voices, "CONCURRENCY", 1)
    await voices.voice_and_export(job_id, None)
    assert seen[-1] > seen[0]


async def test_an_unexpected_error_never_leaves_the_episode_busy(db_sessionmaker, monkeypatch, tmp_path):
    job_id, ids = await _project(db_sessionmaker, monkeypatch, tmp_path)
    _voicing(monkeypatch)

    async def broken_mix(job_id, mute_original, export_path=None):
        raise RuntimeError("disk full")
    monkeypatch.setattr(voices.tts_routes, "_mix_job_task", broken_mix)
    await voices.voice_and_export(job_id, None)
    async with db_sessionmaker() as s:
        job = (await s.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert job.status == JobStatus.COMPLETED and "disk full" in job.error_msg


async def test_a_voice_whose_file_is_gone_is_made_again(db_sessionmaker, monkeypatch, tmp_path):
    job_id, ids = await _project(db_sessionmaker, monkeypatch, tmp_path)   # ids[1]'s /v/2.wav doesn't exist
    voiced, mixed = _voicing(monkeypatch)
    await voices.voice_and_export(job_id, None)
    assert ids[1] in voiced
