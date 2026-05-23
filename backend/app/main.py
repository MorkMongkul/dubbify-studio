"""
app/main.py
FastAPI application entry point.
Registers all routers and startup/shutdown events.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

from app.core.config import settings
from app.core.database import init_db
from app.api.routes import health, projects, jobs, segments, tts

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

# ── Routers ───────────────────────────────────────────────────
app.include_router(health.router)
app.include_router(projects.router,  prefix="/api/v1")
app.include_router(jobs.router,      prefix="/api/v1")
app.include_router(segments.router,  prefix="/api/v1")
app.include_router(tts.router,       prefix="/api/v1")

# ── Root ──────────────────────────────────────────────────────
@app.get("/", include_in_schema=False)
async def root():
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
    }
