"""
app/schemas/schemas.py
Pydantic v2 schemas for all API request bodies and responses.
"""
from pydantic import BaseModel, Field, computed_field, field_validator
import re as _re

# Overlay colours end up inside an ffmpeg filter graph — allow only a plain
# colour name or a hex value, never filter syntax (',', ';', ':', '[', '=').
_COLOR_RE = _re.compile(r"^(|[A-Za-z]{1,20}|#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?|0x[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?)$")


def _check_color(value):
    if value is not None and not _COLOR_RE.match(value):
        raise ValueError("Use a colour name (e.g. white) or a hex value (e.g. #ff0000)")
    return value
from typing import Literal, Optional, List
from datetime import datetime
from app.models.models import JobStatus, Gender, AgeGroup, VoiceMode
from app.services.voice_tags import AGE_GROUPS, GENDERS, normalize_tags, voice_section
from pathlib import Path
from app.core.config import settings
from app.services.source_separator import UNAVAILABLE_MARKER
from app.services import job_clock
from app.core.config import settings


# ── Project schemas ───────────────────────────────────────────
class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    source_lang: str = "zh"
    target_lang: str = "km"
    # Optional "About this drama" text; it starts the project's story notes
    story_intro: str = Field("", max_length=4000)


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    chat_edit_mode: Optional[str] = Field(None, pattern="^(manual|auto)$")


class ProjectResponse(BaseModel):
    id: str
    name: str
    description: str
    source_lang: str
    target_lang: str
    logo_path: str = ""
    chat_edit_mode: str = "manual"
    created_at: datetime
    updated_at: datetime

    @computed_field
    def logo_url(self) -> Optional[str]:
        if not self.logo_path:
            return None
        parts = Path(self.logo_path).parts
        if "uploads" in parts:
            idx = parts.index("uploads")
            return "/" + "/".join(parts[idx:])
        return None

    model_config = {"from_attributes": True}


# ── Job schemas ───────────────────────────────────────────────
class JobResponse(BaseModel):
    id: str
    project_id: str
    status: JobStatus
    progress: int
    error_msg: str
    title: str = ""
    video_path: str
    audio_path: str
    output_path: str
    duration_secs: float
    bgm_volume_db: float
    created_at: datetime
    completed_at: Optional[datetime]
    finished_at: Optional[datetime] = None

    @computed_field
    def video_url(self) -> Optional[str]:
        if not self.video_path:
            return None
        p = Path(self.video_path)
        try:
            parts = p.parts
            if "uploads" in parts:
                idx = parts.index("uploads")
                return "/" + "/".join(parts[idx:])
            return f"/uploads/{p.name}"
        except Exception:
            return None

    @computed_field
    def stems_separated(self) -> Optional[bool]:
        """None until stage 1 made the tracks; False when the original audio stands in for both."""
        job_dir = Path(settings.UPLOAD_DIR) / self.project_id / self.id
        if not (job_dir / "no_vocals.wav").exists():
            return None
        return not (job_dir / UNAVAILABLE_MARKER).exists()

    @computed_field
    def step_elapsed_secs(self) -> Optional[float]:
        """How long the running step has been going (None if unknown), for the editor's progress estimate."""
        seconds = job_clock.elapsed(self.id, self.status)
        return round(seconds, 1) if seconds is not None else None

    @computed_field
    def stems_version(self) -> int:
        """Changes when the tracks are made again, so the editor reloads their audio."""
        try:
            return int((Path(settings.UPLOAD_DIR) / self.project_id / self.id / "no_vocals.wav").stat().st_mtime)
        except OSError:
            return 0

    @computed_field
    def output_url(self) -> Optional[str]:
        if not self.output_path:
            return None
        p = Path(self.output_path)
        try:
            parts = p.parts
            if "uploads" in parts:
                idx = parts.index("uploads")
                return "/" + "/".join(parts[idx:])
            return f"/uploads/{p.name}"
        except Exception:
            return None

    model_config = {"from_attributes": True}


class JobStatusUpdate(BaseModel):
    status: JobStatus
    progress: int = 0
    error_msg: str = ""


class BgmVolumeUpdate(BaseModel):
    volume_db: float = Field(..., ge=-60, le=24)


class JobTitleUpdate(BaseModel):
    title: str = Field(..., max_length=255)


# ── Speaker schemas ───────────────────────────────────────────
class SpeakerCreate(BaseModel):
    label: Optional[str] = None   # auto-generated server-side when absent (manual creation)
    display_name: str = ""
    gender: Gender = Gender.UNKNOWN
    age_group: AgeGroup = AgeGroup.ADULT
    voice_design_prompt: str = ""
    voice_id: Optional[str] = None   # a library voice chosen when the speaker is added


class SpeakerUpdate(BaseModel):
    display_name: Optional[str] = None
    gender: Optional[Gender] = None
    age_group: Optional[AgeGroup] = None
    voice_design_prompt: Optional[str] = None
    voice_id: Optional[str] = None


class SpeakerResponse(BaseModel):
    id: str
    project_id: str
    label: str
    display_name: str
    gender: Gender
    age_group: AgeGroup
    voice_design_prompt: str
    reference_audio_path: str
    voice_id: Optional[str]
    voice_auto: bool = False         # the app picked voice_id; nobody chose it

    @field_validator("voice_auto", mode="before")
    @classmethod
    def _not_picked(cls, value):
        return bool(value)           # NULL on speakers from before 0.2.4

    model_config = {"from_attributes": True}


# ── Segment schemas ───────────────────────────────────────────
class SegmentCreate(BaseModel):
    speaker_id: Optional[str] = None
    voice_id: Optional[str] = None
    lane_index: Optional[int] = None
    start_time: float = Field(..., ge=0)
    end_time: float = Field(..., ge=0)
    source_text: str = ""
    english_text: str = ""
    khmer_text: str = ""
    notes: str = ""


class SegmentUpdate(BaseModel):
    speaker_id: Optional[str] = None
    voice_id: Optional[str] = None
    lane_index: Optional[int] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    source_text: Optional[str] = None
    english_text: Optional[str] = None
    khmer_text: Optional[str] = None
    tts_audio_path: Optional[str] = None   # set "" to clear a stale clip after a text edit
    is_approved: Optional[bool] = None
    notes: Optional[str] = None
    volume_db: Optional[float] = None
    voice_filter: Optional[str] = None
    voice_speed: Optional[float] = None


class SegmentResponse(BaseModel):
    id: str
    job_id: str
    speaker_id: Optional[str]
    voice_id: Optional[str]
    lane_index: Optional[int]
    start_time: float
    end_time: float
    source_text: str
    english_text: str
    khmer_text: str
    tts_audio_path: str
    tts_duration_secs: float
    is_approved: bool
    notes: str
    volume_db: float
    voice_filter: str
    voice_speed: float

    @computed_field
    def tts_audio_url(self) -> Optional[str]:
        if not self.tts_audio_path:
            return None
        p = Path(self.tts_audio_path)
        try:
            parts = p.parts
            if "uploads" in parts:
                idx = parts.index("uploads")
                return "/" + "/".join(parts[idx:])
            return f"/uploads/{p.name}"
        except Exception:
            return None

    model_config = {"from_attributes": True}


# ── Overlay schemas ─────────────────────────────────────────────
class OverlayUpdate(BaseModel):
    x: Optional[float] = None
    y: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None
    opacity: Optional[float] = None
    z_index: Optional[int] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    font_size: Optional[int] = None
    color: Optional[str] = None
    outline_color: Optional[str] = None
    background_color: Optional[str] = None
    blur: Optional[bool] = None

    @field_validator("color", "outline_color", "background_color")
    @classmethod
    def _valid_colors(cls, value):
        return _check_color(value)


class OverlaySubtitleCreate(BaseModel):
    x: float = 0.05
    y: float = 0.8
    width: float = 0.9
    height: float = 0.15
    font_size: int = 42
    color: str = "white"
    outline_color: str = "black"
    background_color: str = ""

    @field_validator("color", "outline_color", "background_color")
    @classmethod
    def _valid_colors(cls, value):
        return _check_color(value)


class OverlayShapeCreate(BaseModel):
    x: float = 0.1
    y: float = 0.85
    width: float = 0.8
    height: float = 0.1
    color: str = "black"
    opacity: float = 0.85
    blur: bool = False

    @field_validator("color")
    @classmethod
    def _valid_color(cls, value):
        return _check_color(value)


class OverlayResponse(BaseModel):
    id: str
    job_id: str
    type: str
    media_path: str
    x: float
    y: float
    width: float
    height: float
    opacity: float
    z_index: int
    start_time: Optional[float]
    end_time: Optional[float]
    font_size: int
    color: str
    outline_color: str
    background_color: str
    blur: bool

    @computed_field
    def media_url(self) -> Optional[str]:
        if not self.media_path:
            return None
        parts = Path(self.media_path).parts
        if "uploads" in parts:
            idx = parts.index("uploads")
            return "/" + "/".join(parts[idx:])
        return None

    model_config = {"from_attributes": True}


# ── Overlay template ("brand kit") schemas ──────────────────────
class OverlayTemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    from_job_id: str
    set_default: bool = False


class OverlayTemplateUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    is_default: Optional[bool] = None


class OverlayTemplateResponse(BaseModel):
    id: str
    name: str
    is_default: bool
    items: List[dict]

    @computed_field
    def item_count(self) -> int:
        return len(self.items)

    model_config = {"from_attributes": True}


# ── TTS request schema (for calling VoxCPM2) ─────────────────
class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1)
    voice_design: str = ""
    language: str = "km"
    cfg_value: float = Field(2.0, ge=0.5, le=5.0)
    inference_timesteps: int = Field(10, ge=5, le=50)


class TTSResponse(BaseModel):
    segment_id: str
    audio_path: str
    duration_secs: float
    success: bool
    error: str = ""
    engine: str = ""            # voxcpm_gradio / voxcpm_hf_space / gemini / mock
    backup_voice: bool = False  # Gemini's stock voice stood in for the chosen VoxCPM voice


# ── Pipeline trigger ──────────────────────────────────────────
class PipelineStartResponse(BaseModel):
    job_id: str
    message: str
    status: JobStatus


# ── Health check ─────────────────────────────────────────────
class HealthResponse(BaseModel):
    status: str
    version: str
    services: dict


# ── Voice library (Voice Creator) ─────────────────────────────
class VoiceTagFields(BaseModel):
    """Who a voice sounds like, and its tags (app/services/voice_tags.py)."""
    gender: Optional[Literal[GENDERS]] = None          # type: ignore[valid-type]
    age_group: Optional[Literal[AGE_GROUPS]] = None    # type: ignore[valid-type]
    personality: Optional[List[str]] = None
    roles: Optional[List[str]] = None

    @field_validator("personality", "roles")
    @classmethod
    def _clean_tags(cls, tags: Optional[List[str]]) -> Optional[List[str]]:
        return None if tags is None else normalize_tags(tags)


class VoiceUpdate(VoiceTagFields):
    name: Optional[str] = None
    mode: Optional[VoiceMode] = None
    description: Optional[str] = None
    reference_transcript: Optional[str] = None
    cfg_value: Optional[float] = Field(None, ge=0.5, le=5.0)
    inference_timesteps: Optional[int] = Field(None, ge=5, le=50)
    seed: Optional[int] = None


class VoiceResponse(BaseModel):
    id: str
    name: str
    mode: str
    description: str
    reference_audio_path: str
    reference_transcript: str
    cfg_value: float
    inference_timesteps: int
    seed: int
    created_at: datetime
    gender: Optional[str] = None
    age_group: Optional[str] = None
    personality: List[str] = []
    roles: List[str] = []

    @field_validator("personality", "roles", mode="before")
    @classmethod
    def _no_tags_yet(cls, tags):
        return tags or []          # NULL on voices made before tags existed

    @computed_field
    def section(self) -> Optional[str]:
        """Voices-tab section; None = "My voices" (no "who" chosen yet)."""
        return voice_section(self.gender, self.age_group, self.roles)

    @computed_field
    def has_reference(self) -> bool:
        return bool(self.reference_audio_path)

    @computed_field
    def builtin(self) -> bool:
        """Ships with the app: can't be renamed, edited or deleted."""
        return self.id.startswith("builtin-")

    @computed_field
    def reference_audio_url(self) -> Optional[str]:
        if not self.reference_audio_path:
            return None
        p = Path(self.reference_audio_path)
        parts = p.parts
        if "uploads" in parts:
            idx = parts.index("uploads")
            return "/" + "/".join(parts[idx:])
        return f"/uploads/{p.name}"

    model_config = {"from_attributes": True}


class VoicePreviewRequest(BaseModel):
    text: str = Field(..., min_length=1)


# ── Assistant chat ─────────────────────────────────────────────
class ChatMessageResponse(BaseModel):
    id: str
    project_id: str
    role: str
    text: str
    function_call: Optional[dict] = None
    function_response: Optional[dict] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ChatSendRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


class ChatSendResponse(BaseModel):
    messages: List[ChatMessageResponse]   # every new row this turn produced (user msg + any tool steps + final reply)


class ChatProposedEditResponse(BaseModel):
    id: str
    project_id: str
    segment_id: str
    message_id: Optional[str]
    old_khmer_text: str
    new_khmer_text: str
    reason: str
    status: str
    created_at: datetime
    resolved_at: Optional[datetime]

    model_config = {"from_attributes": True}
