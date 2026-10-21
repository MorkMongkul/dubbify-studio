"""
app/main.py
FastAPI application entry point.
Registers all routers and startup/shutdown events.
"""
import sys
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from contextlib import asynccontextmanager
import logging

from app.core.config import settings
from app.core.data_folder import follow_moved_data_folder
from app.core.database import init_db
from app.services.pipeline_common import recover_orphaned_jobs
from app.services.builtin_voices import seed_builtin_voices
from app.api.routes import health, projects, jobs, segments, tts, voices, overlays, chat, story
from app.api.routes import settings as settings_routes

# ── Logging setup ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


# ── Lifespan (startup / shutdown) ─────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.APP_NAME} v{settings.version}")
    await init_db()
    logger.info("Database tables initialised.")
    if settings.DUBIFY_DATA_DIR and settings.DUBIFY_PREVIOUS_DATA_DIR:
        try:
            await follow_moved_data_folder(settings.DUBIFY_PREVIOUS_DATA_DIR, settings.DUBIFY_DATA_DIR)
        except Exception:
            # Not fatal: old locations still resolve (the app leaves a link
            # behind, and resolve_media_path remaps paths under .../uploads/).
            logger.exception("Couldn't update stored file locations after the data folder moved")
    try:
        await seed_builtin_voices()
    except Exception:
        # Not fatal: the editor works without them; the next launch retries.
        logger.exception("Couldn't install the built-in voices")
    await recover_orphaned_jobs()
    yield
    logger.info("Shutting down.")


# ── App instance ──────────────────────────────────────────────
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.version,
    description=(
        "Production-grade dubbing studio API.\n\n"
        "Pipeline: Video upload → ASR (Whisper) → "
        "Speaker diarization → Translation (NLLB-200) → "
        "TTS synthesis (VoxCPM2) → Final dubbed video."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── GZip (API responses only) ─────────────────────────────────
# JSON payloads (e.g. a film's full segment list) compress ~80%. Applied only
# to /api paths: /uploads media must stay uncompressed so the byte-range
# requests behind video/audio seeking keep working.
from starlette.middleware.gzip import GZipMiddleware


class APIGZipMiddleware:
    def __init__(self, app):
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=1024)

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path", "").startswith("/api/"):
            await self.gzip(scope, receive, send)
        else:
            await self.app(scope, receive, send)


app.add_middleware(APIGZipMiddleware)

# ── Host check (outermost) ────────────────────────────────────
# No login on a 127.0.0.1 API: validating Host stops a web page from reaching
# it via DNS rebinding (its own domain re-pointed at 127.0.0.1).
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

# ── Routers ───────────────────────────────────────────────────
app.include_router(health.router)
app.include_router(projects.router,  prefix="/api/v1")
app.include_router(story.router,     prefix="/api/v1")
app.include_router(jobs.router,      prefix="/api/v1")
app.include_router(segments.router,  prefix="/api/v1")
app.include_router(tts.router,       prefix="/api/v1")
app.include_router(voices.router,    prefix="/api/v1")
app.include_router(overlays.router,  prefix="/api/v1")
app.include_router(chat.router,      prefix="/api/v1")
app.include_router(settings_routes.router, prefix="/api/v1")

# ── Static Files ──────────────────────────────────────────────
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")


# ── Frontend (built SPA, only present in packaged/production builds) ──
def _frontend_dist_dir() -> Path | None:
    """
    Dev (running from source): repo_root/frontend/dist — absent unless
    `npm run build` was run, in which case the JSON root/ handler below
    is used instead (matches today's dev workflow with a separate Vite
    dev server).

    Frozen (PyInstaller sidecar): bundled via
    `--add-data ../frontend/dist:frontend_dist`, extracted under
    sys._MEIPASS at runtime.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    candidate = (
        Path(meipass) / "frontend_dist"
        if meipass
        else Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    )
    return candidate if candidate.is_dir() else None


FRONTEND_DIST = _frontend_dist_dir()

if FRONTEND_DIST:
    _FRONTEND_ROOT = FRONTEND_DIST.resolve()
    # The desktop window keeps pages it has loaded. Unless the page says to
    # check back every time, an updated app shows the previous version's page,
    # and so its code, until that saved copy expires. Checking back is cheap
    # (a small file from this local server); the JS/CSS files have hashed
    # names, so a new page always points at new ones.
    _CHECK_EVERY_TIME = {"Cache-Control": "no-cache"}

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str):
        # An API or media URL that reached this catch-all doesn't exist —
        # answer with a JSON 404, not the SPA shell.
        if full_path == "api" or full_path.startswith(("api/", "uploads/")):
            raise HTTPException(status_code=404, detail="Not found")
        # The path arrives percent-decoded, so "..%2F" segments or an absolute
        # path would reach any file on disk (including the bundled .env) unless
        # the resolved file is required to stay inside the build.
        candidate = (_FRONTEND_ROOT / full_path).resolve()
        if full_path and candidate.is_relative_to(_FRONTEND_ROOT) and candidate.is_file():
            return FileResponse(candidate, headers=_CHECK_EVERY_TIME if candidate.suffix == ".html" else None)
        # SPA fallback — client-side routes (e.g. /projects/123) resolve to index.html
        return FileResponse(_FRONTEND_ROOT / "index.html", headers=_CHECK_EVERY_TIME)
else:
    @app.get("/", include_in_schema=False)
    async def root():
        return {
            "app": settings.APP_NAME,
            "version": settings.version,
            "docs": "/docs",
        }
