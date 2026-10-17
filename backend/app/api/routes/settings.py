"""
app/api/routes/settings.py
The desktop app's Settings panel: API keys and service choices.

Saved to the user's own settings file in the app-data folder (owner-only
permissions) and applied to the running backend straight away. Secrets are
write-only: the API only ever says whether each one is set. During
development the backend reads backend/.env instead and this API is read-only.
"""
import os
import re
import tempfile
from pathlib import Path
from typing import Annotated, Literal, Optional, Union

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.binaries import find
from app.core.config import settings
from app.services import gemini_models

router = APIRouter(prefix="/settings", tags=["Settings"])

SECRETS = ("GEMINI_API_KEY", "HF_TOKEN")
EDITABLE = (
    "GEMINI_API_KEY", "HF_TOKEN",
    "VOXCPM2_API_URL", "VOXCPM2_HF_SPACE_FALLBACK",
    "TRANSLATION_BACKEND", "GEMINI_MODEL", "GEMINI_ASR_MODEL", "GEMINI_CHAT_MODEL", "GEMINI_TTS_MODEL",
    "TTS_BATCH_CONCURRENCY",
)

# Settings → AI models: which Gemini job each model setting is for. An empty
# value means "Automatic" — the built-in choice (see gemini_models).
MODEL_SETTINGS = {
    "GEMINI_MODEL": ("translation", "Translation"),
    "GEMINI_ASR_MODEL": ("asr", "Transcription and speakers"),
    "GEMINI_CHAT_MODEL": ("chat", "Assistant"),
    "GEMINI_TTS_MODEL": ("tts", "Voice fallback (when VoxCPM is unavailable)"),
}

ModelName = Annotated[str, Field(max_length=100, pattern=r"^[A-Za-z0-9._-]*$")]


class SettingsUpdate(BaseModel):
    """Only the fields sent are changed. An empty secret clears it."""
    model_config = ConfigDict(extra="forbid")

    GEMINI_API_KEY: Annotated[Optional[str], Field(max_length=200)] = None
    HF_TOKEN: Annotated[Optional[str], Field(max_length=200)] = None
    VOXCPM2_API_URL: Annotated[Optional[str], Field(max_length=500)] = None
    VOXCPM2_HF_SPACE_FALLBACK: Annotated[Optional[str], Field(max_length=200, pattern=r"^[A-Za-z0-9._/-]*$")] = None
    TRANSLATION_BACKEND: Optional[Literal["gemini", "deep"]] = None
    GEMINI_MODEL: Optional[ModelName] = None
    GEMINI_ASR_MODEL: Optional[ModelName] = None
    GEMINI_CHAT_MODEL: Optional[ModelName] = None
    GEMINI_TTS_MODEL: Optional[ModelName] = None
    TTS_BATCH_CONCURRENCY: Annotated[Optional[int], Field(ge=1, le=8)] = None

    @field_validator("GEMINI_API_KEY", "HF_TOKEN", "VOXCPM2_API_URL")
    @classmethod
    def _single_line(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and any(c in value for c in "\r\n\0"):
            raise ValueError("must be a single line")
        return value.strip() if value is not None else None

    @field_validator("VOXCPM2_API_URL")
    @classmethod
    def _web_url(cls, value: Optional[str]) -> Optional[str]:
        if value and not re.match(r"^https?://", value):
            raise ValueError("must start with http:// or https://")
        return value


class StorageInfo(BaseModel):
    data_dir: str
    upload_dir: str
    database: Literal["local", "server"]
    log_dir: str


class SettingsView(BaseModel):
    editable: bool
    values: dict[str, Union[str, int]]
    secrets: dict[str, bool]
    storage: StorageInfo
    ffmpeg: Optional[str]
    ffprobe: Optional[str]
    version: str


def _view() -> SettingsView:
    return SettingsView(
        editable=bool(settings.DUBIFY_DATA_DIR),
        values={k: getattr(settings, k) for k in EDITABLE if k not in SECRETS},
        secrets={k: bool(getattr(settings, k)) for k in SECRETS},
        storage=StorageInfo(
            data_dir=settings.DUBIFY_DATA_DIR,
            upload_dir=str(Path(settings.UPLOAD_DIR).resolve()),
            database="local" if settings.effective_db_url.startswith("sqlite") else "server",
            log_dir=settings.DUBIFY_LOG_DIR,
        ),
        ffmpeg=find("ffmpeg"),
        ffprobe=find("ffprobe"),
        version=settings.version,
    )


_PLAIN_VALUE = re.compile(r"[A-Za-z0-9_\-.:/@%+~,]*")
_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _format(value: Union[str, int]) -> str:
    text = str(value)
    if _PLAIN_VALUE.fullmatch(text):
        return text
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _write_settings_file(path: Path, changes: dict) -> None:
    """Update KEY=VALUE lines in place (keeping anything else), atomically, owner-only."""
    lines = path.read_text().splitlines() if path.exists() else [
        "# Movelo Studio settings — written by the app's Settings panel.",
    ]
    pending = dict(changes)
    out = []
    for line in lines:
        match = _ASSIGNMENT.match(line)
        if match and match.group(1) in pending:
            key = match.group(1)
            value = pending.pop(key)
            if not (key in SECRETS and value == ""):
                out.append(f"{key}={_format(value)}")
        else:
            out.append(line)
    for key, value in pending.items():
        if not (key in SECRETS and value == ""):
            out.append(f"{key}={_format(value)}")

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".settings-")   # created 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@router.get("")
def read_settings() -> SettingsView:
    return _view()


@router.put("")
def update_settings(payload: SettingsUpdate) -> SettingsView:
    if not settings.DUBIFY_DATA_DIR:
        raise HTTPException(
            status_code=409,
            detail="During development settings come from backend/.env — edit that file and restart the backend.",
        )
    changes = payload.model_dump(exclude_unset=True)
    if changes:
        _write_settings_file(Path(settings.DUBIFY_DATA_DIR) / "settings.env", changes)
        for key, value in changes.items():
            setattr(settings, key, value)
    return _view()


# ── AI models ─────────────────────────────────────────────────────────────────

class ModelChoice(BaseModel):
    setting: str          # GEMINI_MODEL, …
    label: str
    value: str            # the saved choice; "" = Automatic
    automatic: str        # the model Automatic stands for
    in_use: str           # the model actually answering (differs when a retired one was replaced)
    options: list[str]    # models this key can see that suit this job: aliases, then newest first


class ModelsView(BaseModel):
    choices: list[ModelChoice]


class ModelTest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    setting: Literal["GEMINI_MODEL", "GEMINI_ASR_MODEL", "GEMINI_CHAT_MODEL", "GEMINI_TTS_MODEL"]
    model: Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")]


class ModelTestResult(BaseModel):
    ok: bool
    message: str


def _require_key() -> None:
    if not settings.GEMINI_API_KEY:
        raise HTTPException(status_code=400, detail="Add your Gemini API key first — the models on offer depend on it.")


@router.get("/models")
async def list_models() -> ModelsView:
    """The Gemini models this key can use, per job."""
    _require_key()
    listed = await gemini_models.list_names()
    if not listed:
        raise HTTPException(
            status_code=502,
            detail="Couldn't load the model list from Google — check the API key and your internet connection.",
        )
    def saved(setting: str, purpose: str) -> str:
        value = (getattr(settings, setting) or "").strip()
        return "" if value == gemini_models.default_model(purpose) else value   # untouched = Automatic

    return ModelsView(choices=[
        ModelChoice(
            setting=setting,
            label=label,
            value=saved(setting, purpose),
            automatic=gemini_models.default_model(purpose),
            in_use=gemini_models.current_model(purpose),
            options=gemini_models.options_for(purpose, listed),
        )
        for setting, (purpose, label) in MODEL_SETTINGS.items()
    ])


@router.post("/models/test")
async def test_model(payload: ModelTest) -> ModelTestResult:
    """Check a model with a tiny real request — a listed model can still refuse to work."""
    _require_key()
    ok, message = await gemini_models.probe(MODEL_SETTINGS[payload.setting][0], payload.model)
    return ModelTestResult(ok=ok, message=message)
