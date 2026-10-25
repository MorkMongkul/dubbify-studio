"""
Shared test infrastructure: in-memory SQLite + FastAPI dependency override.

This must live in conftest.py (not a test module): importing it from another
test file under pytest's rootdir import mode creates a SECOND module instance
with its own engine, whose module-level dependency_overrides assignment
silently re-points the app at a database where create_all never ran.
"""
import os
import tempfile

# Before anything imports the app: environment variables override backend/.env
# (pydantic-settings), so no test can reach the developer's real database or
# spend real API quota. Tests that need a "configured" key set it themselves.
_TEST_ROOT = tempfile.mkdtemp(prefix="movelo-tests-")
os.environ.update({
    "DATABASE_URL": f"sqlite+aiosqlite:///{_TEST_ROOT}/unused.db",
    "UPLOAD_DIR": os.path.join(_TEST_ROOT, "uploads"),
    "GEMINI_API_KEY": "",
    "HF_TOKEN": "",
    "VOXCPM2_API_URL": "",
    "ALLOWED_HOSTS": "127.0.0.1,localhost,test,testserver",
})

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import AsyncClient, ASGITransport  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.main import app  # noqa: E402
from app.core.database import get_db, Base  # noqa: E402


TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

# StaticPool: every session must share the ONE connection — a second pooled
# connection would be a separate, empty :memory: database.
test_engine = create_async_engine(
    TEST_DB_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool
)
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


@pytest_asyncio.fixture
def db_sessionmaker():
    """Direct DB access for tests that need rows no API endpoint creates."""
    return TestSessionLocal


@pytest_asyncio.fixture
async def file_db(tmp_path, monkeypatch):
    """
    A file-backed SQLite database with the app's own pool (one connection), for
    tests where several sessions write at once: on the shared in-memory
    connection above, one session closing rolls back another's pending writes.
    Routes and background tasks both use it; returns its sessionmaker.
    """
    import app.core.database as core_db

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/file.db",
                                 connect_args={"timeout": 30}, pool_size=1, max_overflow=0)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def file_get_db():
        async with sessionmaker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = file_get_db
    monkeypatch.setattr(core_db, "AsyncSessionLocal", sessionmaker)
    try:
        yield sessionmaker
    finally:
        app.dependency_overrides[get_db] = override_get_db
        await engine.dispose()


@pytest.fixture(autouse=True)
def _no_speed_measuring(monkeypatch):
    """The face step's speed test (characters.speed) runs the real models: no test may start it, and each starts
    without an answer left by another. A fast machine unless a test says otherwise."""
    from app.services.characters import speed
    monkeypatch.setattr(speed, "_memo", None)
    monkeypatch.setattr(speed, "measure", lambda: speed.Parts(1.0, 1.0, 1.0, 1.0, 1.0))
