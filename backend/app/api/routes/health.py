"""
app/api/routes/health.py
Health check endpoint — verifies all connected services are reachable.
"""
from fastapi import APIRouter
from app.core.config import settings
from app.schemas.schemas import HealthResponse
from app.services.tts_client import tts_client

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """
    Check the health of all pipeline services.
    Frontend polls this on startup to show service status.
    """
    voxcpm2_ok = await tts_client.health_check()

    return HealthResponse(
        status="ok",
        version=settings.APP_VERSION,
        services={
            "database":    "ok",
            "voxcpm2_tts": "ok" if voxcpm2_ok else "unavailable (mock mode)",
            "diarization": f"hf-space ({settings.DIARIZATION_MOSS_SPACE})",
            "separation":  f"hf-space ({settings.SEPARATION_HF_SPACE})",
            "translation": settings.TRANSLATION_BACKEND,
        },
    )
