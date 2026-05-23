"""
tests/test_api.py
Full API integration tests using FastAPI TestClient + in-memory SQLite.
All heavy ML models (Whisper, pyannote, NLLB) are mocked automatically
because no HF_TOKEN or GPU is present in the test environment.
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.main import app
from app.core.database import get_db, Base


# ── Test database (in-memory SQLite) ─────────────────────────
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def override_get_db():
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


app.dependency_overrides[get_db] = override_get_db


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    """Create all tables before each test, drop after."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


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
    resp = await client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert "docs" in data
