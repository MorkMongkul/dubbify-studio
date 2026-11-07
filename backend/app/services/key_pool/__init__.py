"""Several Gemini keys, shared (0.5.8). `get_pool()` is the one pool of the app: its keys are always the ones in
Settings (GEMINI_API_KEY) — it follows a change by itself, so nothing has to tell it — and its numbers are kept in
`gateway.db` in the app's data folder. See pool.py; `client.with_key` is how a request uses it."""
import asyncio
import logging
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.services.key_pool.pool import KeyPool
from app.services.key_pool.store import SQLiteStore

logger = logging.getLogger(__name__)

_store: Optional[SQLiteStore] = None
_pool: Optional[KeyPool] = None
_lock: Optional[asyncio.Lock] = None
_worker: Optional[asyncio.Task] = None


def default_db_path() -> str:
    """gateway.db in the app-data folder (the desktop app), else in the working folder (development)."""
    folder = Path(settings.DUBIFY_DATA_DIR) if settings.DUBIFY_DATA_DIR else Path.cwd()
    return str(folder / "gateway.db")


async def get_pool() -> KeyPool:
    """The pool, made on first use and brought up to date with the keys now in Settings."""
    global _store, _pool, _lock
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        if _pool is None:
            _store = SQLiteStore(default_db_path())
            await _store.init_db()
            _pool = KeyPool(_store)
        keys = settings.parsed_gemini_keys()
        if tuple(keys) != _pool.signature:
            await _pool.sync_keys(keys)
        return _pool


def get_store() -> Optional[SQLiteStore]:
    return _store


def reset() -> None:
    """Forget the pool and stop its worker (tests; the next get_pool starts fresh from the same database)."""
    global _store, _pool, _lock, _worker
    if _worker is not None and not _worker.done():
        _worker.cancel()
    _store = _pool = _lock = _worker = None


async def close() -> None:
    """Shutdown: stop the worker and drop the pool."""
    task = _worker
    reset()
    if task is not None:
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
