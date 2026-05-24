"""
app/core/config.py
Centralised settings — all env vars live here.
Loaded once at startup via pydantic-settings.
"""
from pydantic_settings import BaseSettings
from typing import List
import os


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

    # ── Redis ─────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── File storage ──────────────────────────────────────────
    UPLOAD_DIR: str = "./uploads"
    MAX_UPLOAD_SIZE_MB: int = 500

    # ── pyannoteAI — speaker diarization + transcription ──────
    # Free trial at: dashboard.pyannote.ai
    PYANNOTEAI_TOKEN: str = ""

    # ── Google Gemini — translation ───────────────────────────
    # Free at: aistudio.google.com
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash-lite"

    # ── Translation backend ───────────────────────────────────
    # "gemini" = Gemini batch translation (recommended)
    # "deep"   = Google Translate via deep-translator (fallback)
    TRANSLATION_BACKEND: str = "gemini"

    # ── VoxCPM2 TTS — voice synthesis ────────────────────────
    # Deploy on Lightning AI or RunPod, paste URL here
    VOXCPM2_API_URL: str = ""
    VOXCPM2_API_KEY: str = ""

    # ── Optional services ─────────────────────────────────────
    HF_TOKEN: str = ""
    GROQ_API_KEY: str = ""
    WHISPER_API_URL: str = ""
    DEEPL_API_KEY: str = ""

    # ── CORS ──────────────────────────────────────────────────
    ALLOWED_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    @property
    def cors_origins(self) -> List[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",")]

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()

# Ensure upload directory exists at startup
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)