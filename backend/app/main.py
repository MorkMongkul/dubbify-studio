"""
app/main.py
FastAPI application entry point.
Registers all routers and startup/shutdown events.
"""
import sys
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

from app.core.config import settings
from app.core.database import init_db
from app.services.pipeline_common import recover_orphaned_jobs
from app.api.routes import health, projects, jobs, segments, tts, voices, overlays

# ── Logging setup ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


# ── Lifespan (startup / shutdown) ─────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    await init_db()
    logger.info("Database tables initialised.")
    await recover_orphaned_jobs()
    yield
    logger.info("Shutting down.")


# ── App instance ──────────────────────────────────────────────
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
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

# ── Routers ───────────────────────────────────────────────────
app.include_router(health.router)
app.include_router(projects.router,  prefix="/api/v1")
app.include_router(jobs.router,      prefix="/api/v1")
app.include_router(segments.router,  prefix="/api/v1")
app.include_router(tts.router,       prefix="/api/v1")
app.include_router(voices.router,    prefix="/api/v1")
app.include_router(overlays.router,  prefix="/api/v1")

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
    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str):
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        # SPA fallback — client-side routes (e.g. /projects/123) resolve to index.html
        return FileResponse(FRONTEND_DIST / "index.html")
else:
    @app.get("/", include_in_schema=False)
    async def root():
        return {
            "app": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "docs": "/docs",
        }
