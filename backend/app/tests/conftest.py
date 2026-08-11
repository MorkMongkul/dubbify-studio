"""
Shared test infrastructure: in-memory SQLite + FastAPI dependency override.

This must live in conftest.py (not a test module): importing it from another
test file under pytest's rootdir import mode creates a SECOND module instance
with its own engine, whose module-level dependency_overrides assignment
silently re-points the app at a database where create_all never ran.
"""
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from app.main import app
from app.core.database import get_db, Base


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
