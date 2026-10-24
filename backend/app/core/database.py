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
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import event, inspect, text
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

# Voice tags (app/services/voice_tags.py), added in 0.2.3.
VOICE_TAG_COLUMNS = [("gender", "VARCHAR(10)"), ("age_group", "VARCHAR(10)"), ("personality", "JSON"), ("roles", "JSON")]


async def add_missing_columns(conn: AsyncConnection, table: str, columns: list[tuple[str, str]]) -> list[str]:
    """
    Add the listed columns a table doesn't have yet; return the names added.

    Installed desktop apps never run alembic, so a column added to an
    existing table must also be listed in init_db, or every query on that
    table fails after an update. Only missing columns are altered: on Postgres
    a failing ALTER would abort the whole transaction.
    """
    existing = await conn.run_sync(lambda sync: {c["name"] for c in inspect(sync).get_columns(table)})
    added = []
    for name, col_type in columns:
        if name not in existing:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {col_type}"))
            added.append(name)
    return added


# ── Create all tables on startup ──────────────────────────────
async def init_db(bind: AsyncEngine | None = None):
    bind = bind or engine
    async with bind.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Columns added to existing tables since their first release
    float_type = "DOUBLE PRECISION" if bind.dialect.name == "postgresql" else "FLOAT"
    datetime_type = "TIMESTAMP" if bind.dialect.name == "postgresql" else "DATETIME"
    async with bind.begin() as conn:
        for table, columns in (
            ("segments", [("volume_db", float_type), ("voice_filter", "VARCHAR(50)"), ("voice_speed", float_type)]),
            ("voices", VOICE_TAG_COLUMNS),
            ("speakers", [("voice_auto", "BOOLEAN")]),           # 0.2.4
            ("projects", [("story_notes", "JSON")]),             # 0.2.6
            ("jobs", [("finished_at", datetime_type)]),          # 0.2.6
            ("chat_messages", [("line_ref", "JSON"), ("thread_id", "VARCHAR")]),   # 0.2.6
            ("segments", [("level_gain_db", float_type)]),       # 0.2.8
            ("segments", [("source_start", float_type), ("source_end", float_type), ("voice_offset", float_type),
                          ("fit_rate", float_type), ("voice_text", "TEXT")]),            # 0.3.0
            ("jobs", [("timeline_version", "INTEGER"), ("frame_rate", float_type)]),  # 0.3.0
            ("segments", [("fade_in_secs", float_type), ("fade_out_secs", float_type)]),   # 0.3.1
            ("jobs", [("duck_music", "BOOLEAN")]),                                       # 0.3.1
            ("jobs", [("stem_pieces_made", "BOOLEAN")]),                                 # 0.3.2
            ("jobs", [("track_flags", "TEXT")]),                                         # 0.3.1 tracks
        ):
            for name in await add_missing_columns(conn, table, columns):
                logger.info(f"Added column {name} to the {table} table.")