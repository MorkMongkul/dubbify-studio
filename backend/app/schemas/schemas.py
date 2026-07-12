"""
app/schemas/schemas.py
Pydantic v2 schemas for all API request bodies and responses.
"""
from pydantic import BaseModel, Field, computed_field
from typing import Optional, List
from datetime import datetime
from app.models.models import JobStatus, Gender, AgeGroup, VoiceMode
from pathlib import Path
from app.core.config import settings


# ── Project schemas ───────────────────────────────────────────
class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    source_lang: str = "zh"
    target_lang: str = "km"


class ProjectResponse(BaseModel):
    id: str
    name: str
    description: str
    source_lang: str
    target_lang: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Job schemas ───────────────────────────────────────────────
class JobResponse(BaseModel):
    id: str
    project_id: str
    status: JobStatus
    progress: int
    error_msg: str
    video_path: str
    audio_path: str
    output_path: str
    duration_secs: float
    created_at: datetime
    completed_at: Optional[datetime]

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


# ── Speaker schemas ───────────────────────────────────────────
class SpeakerCreate(BaseModel):
    label: str
    display_name: str = ""
    gender: Gender = Gender.UNKNOWN
    age_group: AgeGroup = AgeGroup.ADULT
    voice_design_prompt: str = ""


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

    model_config = {"from_attributes": True}


# ── Segment schemas ───────────────────────────────────────────
class SegmentUpdate(BaseModel):
    speaker_id: Optional[str] = None
    voice_id: Optional[str] = None
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
class VoiceUpdate(BaseModel):
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

    @computed_field
    def has_reference(self) -> bool:
        return bool(self.reference_audio_path)

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
