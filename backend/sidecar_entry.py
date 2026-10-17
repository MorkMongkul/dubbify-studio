"""
sidecar_entry.py
PyInstaller entrypoint for the desktop app's backend ("sidecar"). Not used in
development (`uvicorn app.main:app`).

The Tauri shell starts it with PORT and the DUBIFY_* variables (see
app/core/config.py) and keeps its stdin open. When the app quits or crashes,
stdin reaches end-of-file and the backend shuts itself down: a PyInstaller
one-file binary runs Python in a child process, so killing the launcher alone
would leave the backend running — holding the database and an old version of
the code that the next launch could end up talking to.
"""
import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler


def watch_parent(stream, on_exit) -> threading.Thread:
    """Call on_exit once `stream` reaches end-of-file (the parent closed its end)."""
    def _run() -> None:
        try:
            while stream.read(4096):
                pass
        except (OSError, ValueError):
            pass
        on_exit()

    thread = threading.Thread(target=_run, name="parent-watchdog", daemon=True)
    thread.start()
    return thread


def raise_open_file_limit(target: int = 10240) -> int:
    """
    An app opened from Finder may only have 256 files open, and every ffmpeg
    the backend starts inherits that. Raise the soft limit as far as the hard
    limit allows (10240 is macOS's per-process maximum). Returns the limit now
    in effect.
    """
    import resource

    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    want = target if hard == resource.RLIM_INFINITY else min(target, hard)
    if soft < want:
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))
        except (ValueError, OSError):
            pass
    return resource.getrlimit(resource.RLIMIT_NOFILE)[0]


def _log_to_file(log_dir: str) -> None:
    os.makedirs(log_dir, exist_ok=True)
    handler = RotatingFileHandler(
        os.path.join(log_dir, "backend.log"), maxBytes=5_000_000, backupCount=3, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"))
    logging.getLogger().addHandler(handler)


def text_shaping() -> str:
    """Whether burned-in Khmer subtitles can be shaped (Pillow's raqm layout needs FriBiDi)."""
    from PIL import features

    if features.check("raqm"):
        return f"ok (FriBiDi {features.version('fribidi')})"
    return "MISSING: burned-in Khmer subtitles would be drawn unshaped (no FriBiDi)"


def self_test() -> int:
    """`movelo-backend --self-test`: checks the packaged backend before it ships."""
    import tempfile

    import shutil

    # Load the app the way the desktop shell starts it, against a throwaway folder.
    data_dir = tempfile.mkdtemp(prefix="movelo-self-test-")
    os.environ.setdefault("DUBIFY_DATA_DIR", data_dir)
    try:
        import app.main  # noqa: F401 — every module the backend loads at start-up

        shaping = text_shaping()
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
    print(f"backend imports: ok\nKhmer text shaping: {shaping}")
    return 0 if shaping.startswith("ok") else 1


def main() -> None:
    if "--self-test" in sys.argv:
        sys.exit(self_test())

    import platform

    import uvicorn

    from app.core.binaries import find
    from app.core.config import settings
    from app.main import app

    if settings.DUBIFY_LOG_DIR:
        _log_to_file(settings.DUBIFY_LOG_DIR)
    log = logging.getLogger("sidecar")
    log.info("──── Movelo Studio backend %s starting (launch %s, %s %s) ────",
             settings.version, settings.DUBIFY_INSTANCE_ID or "-", platform.system(), platform.machine())
    log.info("data: %s | uploads: %s | database: %s",
             settings.DUBIFY_DATA_DIR or "(development)", settings.UPLOAD_DIR,
             "local" if settings.effective_db_url.startswith("sqlite") else "server")
    log.info("ffmpeg: %s | ffprobe: %s", find("ffmpeg") or "NOT FOUND", find("ffprobe") or "NOT FOUND")
    log.info("open files allowed: %d", raise_open_file_limit())
    log.info("Khmer text shaping: %s", text_shaping())

    port = int(os.environ.get("PORT", "8756"))
    # log_config=None: uvicorn logs through the root logger (and so the file);
    # access_log off — the UI polls every few seconds and would flood it.
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, access_log=False))

    if os.environ.get("DUBIFY_EXIT_WITH_PARENT") == "1":
        def _shutdown() -> None:
            log.warning("The desktop app is gone — shutting the backend down")
            server.should_exit = True
            threading.Timer(10, lambda: os._exit(0)).start()   # hard stop if a task hangs

        watch_parent(sys.stdin.buffer, _shutdown)

    server.run()


if __name__ == "__main__":
    main()
