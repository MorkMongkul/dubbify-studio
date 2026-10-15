"""
app/api/routes/health.py
Health check endpoint — verifies all connected services are reachable.
"""
from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from app.core.binaries import find
from app.core.config import settings
from app.schemas.schemas import HealthResponse
from app.services.tts_client import tts_client

router = APIRouter(tags=["Health"])


class ReadyResponse(BaseModel):
    status: str
    instance: str
    version: str


@router.get("/health/ready")
async def ready() -> ReadyResponse:
    """
    Cheap startup probe (no network calls). The desktop app passes a fresh
    DUBIFY_INSTANCE_ID on every launch and only shows the editor once this
    echoes it back — so it never attaches to a leftover backend from an
    earlier launch that happens to answer on the same port.
    """
    return ReadyResponse(status="ok", instance=settings.DUBIFY_INSTANCE_ID, version=settings.version)


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """
    Check the health of all pipeline services.
    Frontend polls this on startup to show service status.
    """
    voxcpm2_ok = await tts_client.health_check()
    from app.core.database import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        database = "ok"
    except Exception as e:
        database = f"error: {type(e).__name__}"

    return HealthResponse(
        status="ok",
        version=settings.version,
        services={
            "database":    database,
            "ffmpeg":      find("ffmpeg") or "missing — install ffmpeg",
            "voxcpm2_tts": "ok" if voxcpm2_ok else "unavailable (mock mode)",
            "diarization": f"hf-space ({settings.DIARIZATION_MOSS_SPACE})",
            "separation":  f"hf-space ({settings.SEPARATION_HF_SPACE})",
            "translation": settings.TRANSLATION_BACKEND,
        },
    )
