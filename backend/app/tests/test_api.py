"""
tests/test_api.py
Full API integration tests using FastAPI TestClient + in-memory SQLite.
All cloud AI services (MOSS diarization, VoxCPM2 TTS, Gemini) fall back to
mocks automatically because no API keys are present in the test environment.
Test DB engine, `client`, and `setup_db` fixtures live in conftest.py.
"""
import pytest


# ── Health ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_health_check(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "services" in data


# ── Projects ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_create_project(client):
    resp = await client.post("/api/v1/projects/", json={
        "name": "Test Chinese Movie",
        "description": "Test project",
        "source_lang": "zh",
        "target_lang": "km",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Test Chinese Movie"
    assert data["source_lang"] == "zh"
    assert data["target_lang"] == "km"
    assert "id" in data


@pytest.mark.asyncio
async def test_list_projects_empty(client):
    resp = await client.get("/api/v1/projects/")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_project_not_found(client):
    resp = await client.get("/api/v1/projects/nonexistent-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_project(client):
    create = await client.post("/api/v1/projects/", json={
        "name": "Before", "description": "keep me",
    })
    project_id = create.json()["id"]

    resp = await client.patch(f"/api/v1/projects/{project_id}", json={"name": "After"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "After"
    # exclude_unset semantics: fields not sent must remain untouched
    assert data["description"] == "keep me"

    get = await client.get(f"/api/v1/projects/{project_id}")
    assert get.json()["name"] == "After"


@pytest.mark.asyncio
async def test_update_project_not_found(client):
    resp = await client.patch("/api/v1/projects/nonexistent-id", json={"name": "X"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_project(client):
    create = await client.post("/api/v1/projects/", json={"name": "To Delete"})
    project_id = create.json()["id"]

    resp = await client.delete(f"/api/v1/projects/{project_id}")
    assert resp.status_code == 204

    get = await client.get(f"/api/v1/projects/{project_id}")
    assert get.status_code == 404


# ── Speakers ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_list_speakers_empty(client):
    create = await client.post("/api/v1/projects/", json={"name": "P1"})
    project_id = create.json()["id"]

    resp = await client.get(f"/api/v1/projects/{project_id}/speakers")
    assert resp.status_code == 200
    assert resp.json() == []


# ── Jobs ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_list_jobs_empty(client):
    create = await client.post("/api/v1/projects/", json={"name": "P1"})
    project_id = create.json()["id"]

    resp = await client.get(f"/api/v1/jobs/project/{project_id}")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_upload_invalid_file_type(client):
    create = await client.post("/api/v1/projects/", json={"name": "P1"})
    project_id = create.json()["id"]

    resp = await client.post(
        f"/api/v1/jobs/upload/{project_id}",
        files={"file": ("document.pdf", b"fake content", "application/pdf")},
    )
    assert resp.status_code == 400
    assert "Unsupported file type" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_upload_to_nonexistent_project(client):
    resp = await client.post(
        "/api/v1/jobs/upload/nonexistent-project-id",
        files={"file": ("movie.mp4", b"fake video", "video/mp4")},
    )
    assert resp.status_code == 404


# ── Segments ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_list_segments_empty(client):
    resp = await client.get("/api/v1/jobs/fake-job-id/segments")
    assert resp.status_code == 200
    assert resp.json() == []


# ── TTS ───────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_tts_segment_not_found(client):
    resp = await client.post("/api/v1/tts/synthesize/segment/nonexistent-id")
    assert resp.status_code == 404


# ── Root ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_root(client):
    """
    `/` serves the built SPA whenever frontend/dist exists (packaged builds, and
    any dev checkout where `npm run build` has been run) and the JSON info
    payload otherwise. Asserting only the JSON shape made this test fail purely
    because a frontend build was present.
    """
    from app.main import FRONTEND_DIST

    resp = await client.get("/")
    assert resp.status_code == 200
    if FRONTEND_DIST:
        assert "text/html" in resp.headers["content-type"]
    else:
        assert "docs" in resp.json()
