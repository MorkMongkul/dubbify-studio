"""
app/core/config.py
Centralised settings — all env vars live here.
Loaded once at startup via pydantic-settings.
"""
from pydantic_settings import BaseSettings
from pydantic import field_validator
from typing import List
import os


class Settings(BaseSettings):
    # ── App ───────────────────────────────────────────────────
    APP_NAME: str = "KhmerDubber Studio"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    SECRET_KEY: str = "change-me-in-production"

    # ── Database ──────────────────────────────────────────────
    # Falls back to local SQLite when empty (great for dev/testing)
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

    # ── GPU service URLs ──────────────────────────────────────
    VOXCPM2_API_URL: str = ""
    WHISPER_API_URL: str = ""       # empty = run Whisper locally
    VOXCPM2_API_KEY: str = ""

    # ── HuggingFace ───────────────────────────────────────────
    HF_TOKEN: str = ""

    # ── pyannoteAI (speaker diarization cloud API) ────────────
    PYANNOTEAI_TOKEN: str = ""

    # ── Groq (Whisper ASR — free at console.groq.com) ────────
    GROQ_API_KEY: str = ""

    # ── Google Gemini (translation — aistudio.google.com) ────
    GEMINI_API_KEY: str = ""

    # ── Translation ───────────────────────────────────────────
    TRANSLATION_BACKEND: str = "nllb"   # "nllb" | "deepl"
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