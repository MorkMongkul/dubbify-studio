"""
app/core/config.py
Centralised settings — all env vars live here.
Loaded once at startup via pydantic-settings.
"""
from pydantic_settings import BaseSettings
from typing import List
import os
import sys
from pathlib import Path


def _env_file_path() -> str:
    """
    Dev: repo-relative `.env` next to this file's project root (unchanged
    behavior — relies on the process being started with cwd=backend/).

    Frozen (PyInstaller sidecar): the working directory Tauri spawns the
    sidecar with is not backend/, so a bare relative ".env" would never be
    found — resolve it next to the bundled data under sys._MEIPASS instead.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return str(Path(meipass) / ".env")
    return ".env"


class Settings(BaseSettings):
    # ── App ───────────────────────────────────────────────────
    APP_NAME: str = "Dubbify Studio API"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    SECRET_KEY: str = "change-me-in-production"

    # ── Database ──────────────────────────────────────────────
    # Leave empty to use SQLite (dev). Set postgresql+asyncpg:// for production.
    DATABASE_URL: str = ""

    @property
    def effective_db_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return "sqlite+aiosqlite:///./khmer_dubber_dev.db"

    # ── File storage ──────────────────────────────────────────
    UPLOAD_DIR: str = "./uploads"
    MAX_UPLOAD_SIZE_MB: int = 500

    # ── Diarization + transcription (free HF Space) ───────────
    # OpenMOSS-Team/MOSS-transcribe-diarize — combined diarization +
    # transcription in one call, no auth, up to ~1800s per call (longer audio
    # is chunked). Falls back to the mock diarizer on failure.
    DIARIZATION_MOSS_SPACE: str = "OpenMOSS-Team/MOSS-transcribe-diarize"

    # ── Google Gemini — translation ───────────────────────────
    # Free at: aistudio.google.com
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash-lite"

    # ── Translation backend ───────────────────────────────────
    # "gemini" = Gemini batch translation (recommended)
    # "deep"   = Google Translate via deep-translator (fallback)
    TRANSLATION_BACKEND: str = "gemini"

    # ── VoxCPM2 TTS — voice synthesis ────────────────────────
    # A Gradio app URL running VoxCPM (e.g. the Colab notebook's gradio.live
    # URL). Leave blank to use the free public HF Space below.
    VOXCPM2_API_URL: str = ""
    # Free public HF Space used automatically when VOXCPM2_API_URL is blank —
    # i.e. whenever the Colab notebook isn't running that session. Real VoxCPM2
    # quality with zero setup; falls back to Gemini TTS if the Space is busy/down.
    VOXCPM2_HF_SPACE_FALLBACK: str = "openbmb/VoxCPM-Demo"

    # ── Source separation (vocals / background, HF Space) ─────
    # Compute happens in the cloud via gradio_client; no local separation.
    SEPARATION_HF_SPACE: str = "PatPatronus/vocal-separation"
    # Model on the HF space: BS-RoFormer | Mel-RoFormer | HTDemucs-FT
    SEPARATION_HF_MODEL: str = "BS-RoFormer"

    # Gemini TTS speaking rate: 1.0 = normal, 1.25 = 25% faster, max 4.0
    GEMINI_TTS_SPEED: float = 1.25

    # ── Optional services ─────────────────────────────────────
    # HF token: raises rate limits / enables private Spaces for the
    # separation + diarization gradio_client calls.
    HF_TOKEN: str = ""

    # ── CORS ──────────────────────────────────────────────────
    ALLOWED_ORIGINS: str = "http://localhost:5173,http://localhost:5174,http://localhost:3000"

    @property
    def cors_origins(self) -> List[str]:
        # Filter out empties so a trailing comma can't inject a "" origin
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    model_config = {
        "env_file": _env_file_path(),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()

# Ensure upload directory exists at startup.
# Never fatal: this runs at import, and a relative UPLOAD_DIR resolved against a
# read-only working directory (which is what the packaged sidecar can be
# launched with) would otherwise raise here and take the whole app down before
# it can serve a single request.
try:
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
except OSError as exc:  # pragma: no cover - depends on launch environment
    import logging
    logging.getLogger(__name__).warning(
        "Could not create UPLOAD_DIR %r (%s) — set an absolute UPLOAD_DIR in .env",
        settings.UPLOAD_DIR, exc,
    )