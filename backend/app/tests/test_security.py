"""
Local-API hardening. The backend runs on 127.0.0.1 with no login, so it must
not hand out arbitrary files, accept forged Host headers (DNS rebinding), or
let a request point the server at files outside the session it belongs to.
"""
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.database as core_db
import app.main as main
from app.api.routes import tts as tts_routes
from app.core.config import settings as app_settings
from app.core.database import Base, get_db
from app.models.models import Job, JobStatus, Project, Segment

REPO_ROOT = Path(__file__).resolve().parents[3]
needs_built_frontend = pytest.mark.skipif(main.FRONTEND_DIST is None, reason="frontend/dist not built")


# ── Static frontend route ─────────────────────────────────────────────────────

@needs_built_frontend
@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/..%2F..%2FCLAUDE.md", "/%2Fetc%2Fhosts", "/..%2F..%2Fbackend%2Fapp%2Fcore%2Fconfig.py"])
async def test_frontend_route_never_serves_files_outside_the_build(client, path):
    resp = await client.get(path)
    body = resp.text
    assert "# CLAUDE.md" not in body
    assert "localhost" not in body or "<html" in body.lower()
    assert "pydantic_settings" not in body


@needs_built_frontend
@pytest.mark.asyncio
async def test_unknown_api_path_is_a_json_404_not_the_app_shell(client):
    resp = await client.get("/api/v1/no-such-endpoint")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")


# ── Host header (DNS rebinding) ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_requests_for_a_foreign_host_name_are_rejected():
    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://attacker.example") as evil:
        resp = await evil.get("/health")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_requests_for_localhost_are_accepted():
    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://localhost:8756") as local:
        resp = await local.get("/health")
    assert resp.status_code == 200


# ── File paths supplied by the client ─────────────────────────────────────────

async def _session_with_segment(db_sessionmaker, tts_path=""):
    async with db_sessionmaker() as s:
        project = Project(name="P")
        s.add(project)
        await s.flush()
        job = Job(project_id=project.id, status=JobStatus.COMPLETED, progress=100)
        s.add(job)
        await s.flush()
        seg = Segment(job_id=job.id, start_time=0.0, end_time=1.0, source_text="a",
                      khmer_text="b", tts_audio_path=tts_path)
        s.add(seg)
        await s.commit()
        return project.id, job.id, seg.id


@pytest.mark.asyncio
async def test_segment_audio_path_outside_its_session_is_rejected(client, db_sessionmaker, monkeypatch, tmp_path):
    monkeypatch.setattr(app_settings, "UPLOAD_DIR", str(tmp_path))
    victim = tmp_path / "victim.mp4"
    victim.write_bytes(b"precious")
    _, _, seg_id = await _session_with_segment(db_sessionmaker)

    resp = await client.patch(f"/api/v1/segments/{seg_id}", json={"tts_audio_path": str(victim), "volume_db": 6})
    assert resp.status_code == 400
    assert victim.read_bytes() == b"precious"


@pytest.mark.asyncio
async def test_segment_audio_path_inside_its_session_is_accepted(client, db_sessionmaker, monkeypatch, tmp_path):
    """Undo of a text edit sends the previous clip path back — that must keep working."""
    monkeypatch.setattr(app_settings, "UPLOAD_DIR", str(tmp_path))
    project_id, job_id, seg_id = await _session_with_segment(db_sessionmaker)
    clip = tmp_path / project_id / job_id / "tts" / f"seg_{seg_id}.wav"
    clip.parent.mkdir(parents=True)
    sf.write(clip, np.zeros(2205, dtype=np.float32), 22050)

    resp = await client.patch(f"/api/v1/segments/{seg_id}", json={"tts_audio_path": str(clip)})
    assert resp.status_code == 200
    assert resp.json()["tts_audio_path"] == str(clip)
    cleared = await client.patch(f"/api/v1/segments/{seg_id}", json={"tts_audio_path": ""})
    assert cleared.status_code == 200


@pytest.mark.asyncio
async def test_audio_path_is_stored_in_the_form_that_was_checked(client, db_sessionmaker, monkeypatch, tmp_path):
    """
    A raw path that only *remaps* into the session at check time must not be
    stored as given: once a file appears at the raw location, the stored
    string would resolve there instead, and the next effect change would
    re-render (overwrite) that outside file.
    """
    monkeypatch.setattr(app_settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    project_id, job_id, seg_id = await _session_with_segment(db_sessionmaker)
    real = tmp_path / "uploads" / project_id / job_id / "tts" / f"seg_{seg_id}.wav"
    real.parent.mkdir(parents=True)
    sf.write(real, np.zeros(2205, dtype=np.float32), 22050)
    decoy = tmp_path / "elsewhere" / "uploads" / project_id / job_id / "tts" / f"seg_{seg_id}.wav"

    resp = await client.patch(f"/api/v1/segments/{seg_id}", json={"tts_audio_path": str(decoy)})
    assert resp.status_code == 200
    assert resp.json()["tts_audio_path"] == str(real.resolve())

    decoy.parent.mkdir(parents=True)
    decoy.write_bytes(b"outside file")
    await client.patch(f"/api/v1/segments/{seg_id}", json={"volume_db": 3})
    assert decoy.read_bytes() == b"outside file"


@pytest.mark.asyncio
async def test_audio_path_through_a_symlink_out_of_the_session_is_rejected(client, db_sessionmaker, monkeypatch, tmp_path):
    monkeypatch.setattr(app_settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    project_id, job_id, seg_id = await _session_with_segment(db_sessionmaker)
    tts_dir = tmp_path / "uploads" / project_id / job_id / "tts"
    tts_dir.mkdir(parents=True)
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"outside")
    (tts_dir / "link.wav").symlink_to(outside)
    resp = await client.patch(f"/api/v1/segments/{seg_id}", json={"tts_audio_path": str(tts_dir / "link.wav")})
    assert resp.status_code == 400


def test_export_copy_never_writes_through_a_symlink_placed_after_validation(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    final = tmp_path / "uploads" / "final.mp4"
    final.parent.mkdir()
    final.write_bytes(b"VIDEO")
    victim = tmp_path / "victim.txt"
    victim.write_bytes(b"precious")
    dest = tmp_path / "Movies" / "dub.mp4"

    validated = tts_routes._validated_export_path(str(dest))
    dest.symlink_to(victim)                      # swapped in while the mix runs
    tts_routes._copy_export(str(final), validated)

    assert victim.read_bytes() == b"precious"
    assert dest.read_bytes() == b"VIDEO" and not dest.is_symlink()
    assert [p.name for p in dest.parent.iterdir()] == ["dub.mp4"], "no temp file left behind"


def test_export_copy_rechecks_the_destination_when_writing(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    final = tmp_path / "final.mp4"
    final.write_bytes(b"VIDEO")
    outside = tmp_path / "outside"
    outside.mkdir()
    movies = tmp_path / "home" / "Movies"
    movies.mkdir(parents=True)

    validated = tts_routes._validated_export_path(str(movies / "dub.mp4"))
    movies.rmdir()
    movies.symlink_to(outside)                   # parent swapped for a link out of $HOME
    with pytest.raises(OSError):
        tts_routes._copy_export(str(final), validated)
    assert not (outside / "dub.mp4").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("dest", ["/etc/periodic/daily/evil.mp4", "~/notes.txt", "relative/out.mp4"])
async def test_export_destination_must_be_an_mp4_in_a_user_location(client, db_sessionmaker, monkeypatch, tmp_path, dest):
    monkeypatch.setenv("HOME", str(tmp_path))
    _, job_id, _ = await _session_with_segment(db_sessionmaker, tts_path="/x/seg.wav")
    resp = await client.post(f"/api/v1/tts/mix/{job_id}", json={"export_path": dest})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_export_to_an_mp4_under_home_is_accepted(client, db_sessionmaker, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    started = []

    async def fake_mix(job_id, mute_original, export_path=None):
        started.append(export_path)

    monkeypatch.setattr(tts_routes, "_mix_job_task", fake_mix)
    _, job_id, _ = await _session_with_segment(db_sessionmaker, tts_path="/x/seg.wav")
    resp = await client.post(f"/api/v1/tts/mix/{job_id}", json={"export_path": "~/Movies/dub.mp4"})
    assert resp.status_code == 202
    assert started == [str(tmp_path / "Movies" / "dub.mp4")]


# ── ffmpeg filter-graph strings ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_overlay_colours_cannot_carry_ffmpeg_filter_syntax(client, db_sessionmaker):
    _, job_id, _ = await _session_with_segment(db_sessionmaker)
    bad = await client.post(f"/api/v1/jobs/{job_id}/overlays/shape",
                            json={"color": "black:t=fill,drawtext=textfile=/etc/passwd"})
    assert bad.status_code == 422
    ok = await client.post(f"/api/v1/jobs/{job_id}/overlays/shape", json={"color": "#1a2b3c"})
    assert ok.status_code == 201
    overlay_id = ok.json()["id"]
    assert (await client.patch(f"/api/v1/overlays/{overlay_id}", json={"color": "yellow"})).status_code == 200
    assert (await client.patch(f"/api/v1/overlays/{overlay_id}", json={"background_color": ""})).status_code == 200
    assert (await client.patch(f"/api/v1/overlays/{overlay_id}", json={"color": "red;[x]"})).status_code == 422


# ── Request sessions vs. background tasks on SQLite ───────────────────────────

@pytest.mark.asyncio
async def test_background_work_is_not_starved_of_the_single_sqlite_connection(monkeypatch, tmp_path):
    """
    The dev/desktop SQLite engine has ONE pooled connection. A request's
    session must be closed before its background task runs — otherwise the
    task waits for that connection, times out, and the work silently fails.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/one.db",
                                 pool_size=1, max_overflow=0, pool_timeout=2)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def single_connection_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def fake_synth(text, output_path="", **kwargs):
        sf.write(output_path, np.zeros(2205, dtype=np.float32), 22050)
        return {"success": True, "audio_path": output_path, "duration_secs": 0.1, "error": "", "backend": "voxcpm_gradio"}

    async def no_sleep(_s):
        return None

    monkeypatch.setitem(main.app.dependency_overrides, get_db, single_connection_db)
    monkeypatch.setattr(core_db, "AsyncSessionLocal", factory)
    monkeypatch.setattr(tts_routes.settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(tts_routes.tts_client, "synthesize", fake_synth)
    monkeypatch.setattr(tts_routes.asyncio, "sleep", no_sleep)

    _, _, seg_id = await _session_with_segment(factory)
    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/api/v1/tts/synthesize/batch", json={"segment_ids": [seg_id]})
        assert resp.status_code == 202
    async with factory() as s:
        seg = await s.get(Segment, seg_id)
        assert seg.tts_audio_path, "the background task never got the connection"
    await engine.dispose()
