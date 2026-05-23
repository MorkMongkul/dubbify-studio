"""
app/core/database.py
Async SQLAlchemy engine + session factory.
Supports both SQLite (dev) and PostgreSQL (production).
"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings


# ── Engine ────────────────────────────────────────────────────
engine = create_async_engine(
    settings.effective_db_url,
    echo=settings.DEBUG,
    # SQLite needs check_same_thread=False
    connect_args={"check_same_thread": False}
    if "sqlite" in settings.effective_db_url
    else {},
)

# ── Session factory ───────────────────────────────────────────
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ── Base model class ──────────────────────────────────────────
class Base(DeclarativeBase):
    pass


# ── Dependency for FastAPI routes ─────────────────────────────
async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ── Create all tables on startup ──────────────────────────────
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
