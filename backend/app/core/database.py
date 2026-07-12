"""
app/core/database.py
Async SQLAlchemy engine + session factory.
Supports both SQLite (dev) and PostgreSQL/Neon (production).

SQLite: Uses WAL mode + busy timeout to prevent "database is locked" errors
        when background tasks write while new requests come in.

Neon/PostgreSQL: Uses asyncpg driver, connection pooling via Neon's built-in
        PgBouncer (use the -pooler connection string from Neon console).
        No WAL config needed — Postgres handles concurrency natively.
"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import event, text
from app.core.config import settings


def _is_sqlite() -> bool:
    return "sqlite" in settings.effective_db_url


# ── Engine ────────────────────────────────────────────────────
if _is_sqlite():
    engine = create_async_engine(
        settings.effective_db_url,
        echo=settings.DEBUG,
        connect_args={
            "check_same_thread": False,
            "timeout": 30,
        },
        # SQLite: single writer only, pool size 1 avoids lock contention
        pool_size=1,
        max_overflow=0,
    )

    # WAL mode — allows concurrent reads during writes
    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

else:
    # PostgreSQL / Neon — asyncpg driver
    engine = create_async_engine(
        settings.effective_db_url,
        echo=settings.DEBUG,
        # Pool settings for Neon serverless
        # Neon's pooler handles many connections — keep app pool small
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,      # detect stale connections after cold start
        pool_recycle=300,        # recycle connections every 5 min
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


import logging
logger = logging.getLogger(__name__)

# ── Create all tables on startup ──────────────────────────────
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Run safe column additions for audio effects parameters
    async with engine.begin() as conn:
        for col, col_type in [
            ("volume_db", "DOUBLE PRECISION" if "postgresql" in settings.effective_db_url else "FLOAT"),
            ("voice_filter", "VARCHAR(50)"),
            ("voice_speed", "DOUBLE PRECISION" if "postgresql" in settings.effective_db_url else "FLOAT"),
        ]:
            try:
                await conn.execute(text(f"ALTER TABLE segments ADD COLUMN {col} {col_type}"))
                logger.info(f"Added column {col} to segments table.")
            except Exception:
                # Column likely already exists, ignore error
                pass