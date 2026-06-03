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
    # Deploy on Modal/RunPod (REST), or run the Colab Gradio app and paste its
    # gradio.live URL here.
    VOXCPM2_API_URL: str = ""
    VOXCPM2_API_KEY: str = ""
    # Backend protocol: "rest" (our Modal server) or "gradio" (Colab Gradio app).
    # Leave blank to auto-detect: a "gradio" in the URL → gradio, else rest.
    VOXCPM2_BACKEND: str = ""

    # ── Source separation (vocals / background) ───────────────
    # "local" = Demucs on this machine (heavy on 8GB Macs, can freeze)
    # "hf"    = HuggingFace Space via gradio_client (offloads compute, SOTA models)
    SEPARATION_BACKEND: str = "local"
    SEPARATION_HF_SPACE: str = "PatPatronus/vocal-separation"
    # Model on the HF space: BS-RoFormer | Mel-RoFormer | HTDemucs-FT
    SEPARATION_HF_MODEL: str = "BS-RoFormer"

    # Gemini TTS speaking rate: 1.0 = normal, 1.25 = 25% faster, max 4.0
    GEMINI_TTS_SPEED: float = 1.25

    # ── Optional services ─────────────────────────────────────
    HF_TOKEN: str = ""
    GROQ_API_KEY: str = ""
    WHISPER_API_URL: str = ""
    DEEPL_API_KEY: str = ""

    # ── CORS ──────────────────────────────────────────────────
    ALLOWED_ORIGINS: str = "http://localhost:5173,http://localhost:5174,http://localhost:3000"

    @property
    def cors_origins(self) -> List[str]:
        # Filter out empties so a trailing comma can't inject a "" origin
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()

# Ensure upload directory exists at startup
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)