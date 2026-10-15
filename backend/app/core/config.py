"""
app/core/config.py
Centralised settings — all env vars live here.
Loaded once at startup via pydantic-settings.
"""
from pydantic import model_validator
from pydantic_settings import BaseSettings
from typing import List
import os
from pathlib import Path


def _data_dir() -> str:
    """The app-data folder the desktop shell passes in; empty during development."""
    return os.environ.get("DUBIFY_DATA_DIR", "").strip()


def _env_file_path() -> str:
    """
    Desktop app: the user's own settings file in the app-data folder (written
    by the Settings panel). Nothing is ever read from inside the binary — keys
    baked into a distributed app can be extracted by anyone who has a copy.

    Development: `.env` relative to the working directory (cwd=backend/).
    """
    data = _data_dir()
    return str(Path(data) / "settings.env") if data else ".env"


class Settings(BaseSettings):
    # ── App ───────────────────────────────────────────────────
    APP_NAME: str = "Dubbify Studio API"
    APP_VERSION: str = "0.1.0-dev"
    DEBUG: bool = False

    # ── Desktop shell (set by the Tauri app when it starts the backend) ──
    DUBIFY_DATA_DIR: str = ""      # ~/Library/Application Support/<app id>
    DUBIFY_LOG_DIR: str = ""       # ~/Library/Logs/<app id>
    DUBIFY_INSTANCE_ID: str = ""   # per launch — proves to the app it reached THIS backend
    DUBIFY_APP_VERSION: str = ""   # the app's own version (tauri.conf.json)

    @property
    def version(self) -> str:
        return self.DUBIFY_APP_VERSION or self.APP_VERSION

    # ── Database ──────────────────────────────────────────────
    # Leave empty for SQLite: in the app-data folder on the desktop, ./ in dev.
    # Set postgresql+asyncpg:// only for a server database.
    DATABASE_URL: str = ""

    @property
    def effective_db_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        if self.DUBIFY_DATA_DIR:
            return f"sqlite+aiosqlite:///{Path(self.DUBIFY_DATA_DIR) / 'dubify.db'}"
        return "sqlite+aiosqlite:///./khmer_dubber_dev.db"

    # ── ffmpeg / ffprobe ──────────────────────────────────────
    # Explicit paths — the desktop app points these at the copies bundled
    # inside it. Empty = search PATH plus the Homebrew folders (app/core/binaries.py).
    FFMPEG_PATH: str = ""
    FFPROBE_PATH: str = ""

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

    # ── Assistant chat (project translation-memory chatbot) ────
    # Deliberately separate from GEMINI_MODEL (the batch translation
    # pipeline's model) so the two can be tuned/upgraded independently — the
    # chat needs reliable function-calling, the batch pipeline doesn't.
    # gemini-2.5-flash returns a hard 404 for new API keys as of this build
    # ("no longer available to new users" — confirmed live against the real
    # API, not just docs) — Google's own error pointed at gemini-3.6-flash.
    GEMINI_CHAT_MODEL: str = "gemini-3.6-flash"
    # ── ASR (speech-to-text) ────────────────────────────────────
    # Deliberately separate from GEMINI_MODEL: translation works fine on a
    # cheap "lite" model since the input is already clean text, but audio
    # transcription accuracy needs a full multimodal model.
    GEMINI_ASR_MODEL: str = "gemini-3.6-flash"
    # Hard cap on tool-call round-trips within a single chat turn, so a model
    # stuck in a call/respond/call-again loop can't run away indefinitely.
    CHAT_MAX_TOOL_ROUNDS: int = 6
    # How many prior messages to replay as context on each turn. Unbounded
    # history would make every turn slower and more expensive the longer a
    # project's conversation runs; this caps it at a size still generous
    # enough to cover a real back-and-forth review session.
    CHAT_HISTORY_LIMIT: int = 60

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
    SEPARATION_HF_SPACE: str = "csukuangfj/music-source-separation"

    # Gemini TTS speaking rate: 1.0 = normal, 1.25 = 25% faster, max 4.0
    GEMINI_TTS_SPEED: float = 1.25

    # How many segments to synthesize concurrently during a batch/job TTS
    # run. VoxCPM2's free HF Space fallback runs a real batching inference
    # server (not a naive single-request queue), but its own maintainers'
    # recommendation is to keep concurrency at 1 "unless you have profiled
    # GPU memory headroom" on a Space shared with the public — we can't see
    # from outside whether the live deployment honours that. Start low and
    # raise it only after confirming wall-clock time actually drops without
    # a rise in errors/timeouts for whichever backend you're using.
    TTS_BATCH_CONCURRENCY: int = 2

    # ── Optional services ─────────────────────────────────────
    # HF token: raises rate limits / enables private Spaces for the
    # separation + diarization gradio_client calls.
    HF_TOKEN: str = ""

    # ── CORS ──────────────────────────────────────────────────
    ALLOWED_ORIGINS: str = "http://localhost:5173,http://localhost:5174,http://localhost:3000"

    # ── Host check ────────────────────────────────────────────
    # Host names the API answers to. It listens on 127.0.0.1 without a login,
    # so a request naming any other host — a web page reaching it through DNS
    # rebinding — is refused. Add a name here only if you really serve it under one.
    ALLOWED_HOSTS: str = "127.0.0.1,localhost"

    @property
    def allowed_hosts(self) -> List[str]:
        return [h.strip() for h in self.ALLOWED_HOSTS.split(",") if h.strip()]

    @property
    def cors_origins(self) -> List[str]:
        # Filter out empties so a trailing comma can't inject a "" origin
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @model_validator(mode="after")
    def _desktop_defaults(self):
        # Desktop: media lives in the app-data folder unless explicitly moved
        # (a relative ./uploads would land wherever the app was launched from).
        if self.DUBIFY_DATA_DIR and "UPLOAD_DIR" not in self.model_fields_set:
            self.UPLOAD_DIR = str(Path(self.DUBIFY_DATA_DIR) / "uploads")
        return self

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings(_env_file=_env_file_path())

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