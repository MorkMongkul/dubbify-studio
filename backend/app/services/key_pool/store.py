"""The key pool's SQLite store (from the Gemini Key-Pool Gateway): each key's daily counters and health (dead, cooling,
exhausted), the translation cache and the /gateway translate jobs. Keys are identified by a short hash of the key
itself (key_id), not by their position in the list, so moving or removing a key never hands its counters or its "dead"
mark to another one. WAL mode; one short connection per call."""
import hashlib
import json
import logging
import time
from typing import Optional, List, Dict, Any, Tuple
import aiosqlite

logger = logging.getLogger(__name__)


def compute_cache_key(source_text: str, target_lang: str, model: str) -> str:
    """Return sha256(source_text + target_lang + model)."""
    raw = f"{source_text}{target_lang}{model}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class SQLiteStore:
    def __init__(self, db_path: str = "gateway.db") -> None:
        self.db_path = db_path

    async def init_db(self) -> None:
        """Create tables if they do not exist and enable WAL mode."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            
            # Key persistent state (dead, cooling, exhausted)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS key_metadata (
                    key_id TEXT PRIMARY KEY,
                    is_dead INTEGER DEFAULT 0,
                    cooling_until REAL DEFAULT NULL,
                    exhausted_until REAL DEFAULT NULL,
                    last_updated REAL DEFAULT (strftime('%s', 'now'))
                );
            """)

            # Daily usage counters per key per Pacific day
            await db.execute("""
                CREATE TABLE IF NOT EXISTS key_daily_usage (
                    key_id TEXT,
                    pacific_day TEXT,
                    rpd INTEGER DEFAULT 0,
                    tpd INTEGER DEFAULT 0,
                    PRIMARY KEY (key_id, pacific_day)
                );
            """)

            # Translation cache keyed on sha256(source_text + target_lang + model)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS translation_cache (
                    cache_key TEXT PRIMARY KEY,
                    target_lang TEXT,
                    model TEXT,
                    source_text TEXT,
                    translated_text TEXT,
                    created_at REAL
                );
            """)

            # Job queue / job tracking
            await db.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    video_id TEXT,
                    target_lang TEXT,
                    source_lang TEXT,
                    status TEXT,
                    line_count INTEGER,
                    failed_indices TEXT,
                    request_payload TEXT,
                    result_payload TEXT,
                    error_message TEXT,
                    created_at REAL,
                    updated_at REAL,
                    completed_at REAL
                );
            """)
            await db.commit()

    async def get_key_metadata(self, key_id: str) -> Dict[str, Any]:
        """Fetch persistent state for a key."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT is_dead, cooling_until, exhausted_until FROM key_metadata WHERE key_id = ?",
                (key_id,)
            )
            row = await cursor.fetchone()
            if row:
                return {
                    "is_dead": bool(row["is_dead"]),
                    "cooling_until": row["cooling_until"],
                    "exhausted_until": row["exhausted_until"]
                }
            return {"is_dead": False, "cooling_until": None, "exhausted_until": None}

    async def set_key_dead(self, key_id: str) -> None:
        """Mark key permanently dead."""
        async with aiosqlite.connect(self.db_path) as db:
            now = time.time()
            await db.execute("""
                INSERT INTO key_metadata (key_id, is_dead, last_updated)
                VALUES (?, 1, ?)
                ON CONFLICT(key_id) DO UPDATE SET is_dead = 1, last_updated = ?
            """, (key_id, now, now))
            await db.commit()
            logger.warning(f"Key {key_id} permanently marked as DEAD in database")

    async def set_key_cooling(self, key_id: str, cooling_until: float) -> None:
        """Mark key cooling until a given unix timestamp."""
        async with aiosqlite.connect(self.db_path) as db:
            now = time.time()
            await db.execute("""
                INSERT INTO key_metadata (key_id, cooling_until, last_updated)
                VALUES (?, ?, ?)
                ON CONFLICT(key_id) DO UPDATE SET cooling_until = ?, last_updated = ?
            """, (key_id, cooling_until, now, cooling_until, now))
            await db.commit()

    async def set_key_exhausted(self, key_id: str, exhausted_until: float) -> None:
        """Mark key exhausted until a given unix timestamp (midnight Pacific)."""
        async with aiosqlite.connect(self.db_path) as db:
            now = time.time()
            await db.execute("""
                INSERT INTO key_metadata (key_id, exhausted_until, last_updated)
                VALUES (?, ?, ?)
                ON CONFLICT(key_id) DO UPDATE SET exhausted_until = ?, last_updated = ?
            """, (key_id, exhausted_until, now, exhausted_until, now))
            await db.commit()

    async def clear_key_exhausted(self, key_id: str) -> None:
        """Clear exhausted status for a key when new Pacific day arrives."""
        async with aiosqlite.connect(self.db_path) as db:
            now = time.time()
            await db.execute("""
                UPDATE key_metadata SET exhausted_until = NULL, last_updated = ?
                WHERE key_id = ?
            """, (now, key_id))
            await db.commit()

    async def get_daily_usage(self, key_id: str, pacific_day: str) -> Tuple[int, int]:
        """Return (rpd, tpd) for a key on a given Pacific day."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT rpd, tpd FROM key_daily_usage WHERE key_id = ? AND pacific_day = ?",
                (key_id, pacific_day)
            )
            row = await cursor.fetchone()
            if row:
                return int(row["rpd"]), int(row["tpd"])
            return 0, 0

    async def add_daily_usage(self, key_id: str, pacific_day: str, rpd_delta: int, tpd_delta: int) -> Tuple[int, int]:
        """Atomically increment RPD and TPD and return the new totals."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO key_daily_usage (key_id, pacific_day, rpd, tpd)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key_id, pacific_day) DO UPDATE SET
                    rpd = rpd + ?,
                    tpd = tpd + ?
            """, (key_id, pacific_day, rpd_delta, tpd_delta, rpd_delta, tpd_delta))
            await db.commit()
            
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT rpd, tpd FROM key_daily_usage WHERE key_id = ? AND pacific_day = ?",
                (key_id, pacific_day)
            )
            row = await cursor.fetchone()
            return int(row["rpd"]), int(row["tpd"])

    async def get_cached_translation(self, cache_key: str) -> Optional[str]:
        """Fetch cached translation text if present."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT translated_text FROM translation_cache WHERE cache_key = ?",
                (cache_key,)
            )
            row = await cursor.fetchone()
            if row:
                return row[0]
            return None

    async def set_cached_translation(
        self, cache_key: str, target_lang: str, model: str, source_text: str, translated_text: str
    ) -> None:
        """Store translation in cache."""
        async with aiosqlite.connect(self.db_path) as db:
            now = time.time()
            await db.execute("""
                INSERT INTO translation_cache (cache_key, target_lang, model, source_text, translated_text, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET translated_text = excluded.translated_text
            """, (cache_key, target_lang, model, source_text, translated_text, now))
            await db.commit()

    async def create_job(
        self,
        job_id: str,
        video_id: str,
        target_lang: str,
        source_lang: str,
        line_count: int,
        request_payload: Dict[str, Any]
    ) -> None:
        """Create a job entry in queued state."""
        async with aiosqlite.connect(self.db_path) as db:
            now = time.time()
            await db.execute("""
                INSERT INTO jobs (
                    job_id, video_id, target_lang, source_lang, status, line_count,
                    failed_indices, request_payload, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'queued', ?, '[]', ?, ?, ?)
            """, (job_id, video_id, target_lang, source_lang, line_count, json.dumps(request_payload), now, now))
            await db.commit()

    async def update_job(
        self,
        job_id: str,
        status: str,
        result_payload: Optional[List[Dict[str, Any]]] = None,
        failed_indices: Optional[List[int]] = None,
        error_message: Optional[str] = None
    ) -> None:
        """Update job status and results."""
        async with aiosqlite.connect(self.db_path) as db:
            now = time.time()
            completed_at = now if status in ("done", "failed") else None
            result_json = json.dumps(result_payload) if result_payload is not None else None
            failed_json = json.dumps(failed_indices) if failed_indices is not None else None

            await db.execute("""
                UPDATE jobs SET
                    status = ?,
                    result_payload = COALESCE(?, result_payload),
                    failed_indices = COALESCE(?, failed_indices),
                    error_message = COALESCE(?, error_message),
                    updated_at = ?,
                    completed_at = COALESCE(?, completed_at)
                WHERE job_id = ?
            """, (status, result_json, failed_json, error_message, now, completed_at, job_id))
            await db.commit()

    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get job by ID."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            res = dict(row)
            if res.get("failed_indices"):
                try:
                    res["failed_indices"] = json.loads(res["failed_indices"])
                except Exception:
                    res["failed_indices"] = []
            if res.get("result_payload"):
                try:
                    res["result_payload"] = json.loads(res["result_payload"])
                except Exception:
                    pass
            if res.get("request_payload"):
                try:
                    res["request_payload"] = json.loads(res["request_payload"])
                except Exception:
                    pass
            return res

    async def get_next_queued_job(self) -> Optional[Dict[str, Any]]:
        """Fetch oldest job in queued state."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT * FROM jobs WHERE status = 'queued'
                ORDER BY created_at ASC LIMIT 1
            """)
            row = await cursor.fetchone()
            if not row:
                return None
            res = dict(row)
            if res.get("request_payload"):
                res["request_payload"] = json.loads(res["request_payload"])
            return res

    async def get_recent_jobs(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Fetch the most recent jobs."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT job_id, video_id, target_lang, source_lang, status, line_count,
                       failed_indices, created_at, updated_at, completed_at
                FROM jobs
                ORDER BY created_at DESC LIMIT ?
            """, (limit,))
            rows = await cursor.fetchall()
            jobs = []
            for row in rows:
                item = dict(row)
                if item.get("failed_indices"):
                    try:
                        item["failed_indices"] = json.loads(item["failed_indices"])
                    except Exception:
                        item["failed_indices"] = []
                jobs.append(item)
            return jobs

    async def get_completed_jobs_count_today(self, pacific_day_start_ts: float) -> int:
        """Count how many jobs completed since Pacific midnight."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT COUNT(*) FROM jobs
                WHERE status = 'done' AND completed_at >= ?
            """, (pacific_day_start_ts,))
            row = await cursor.fetchone()
            return row[0] if row else 0

