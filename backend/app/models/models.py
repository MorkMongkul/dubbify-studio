"""
app/models/models.py
All database tables defined here.
"""
from sqlalchemy import (
    Column, String, Integer, Float, Boolean,
    DateTime, ForeignKey, Text, JSON, Enum as SAEnum
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base
import enum
import uuid


def generate_uuid():
    return str(uuid.uuid4())


# ── Enums ─────────────────────────────────────────────────────
class JobStatus(str, enum.Enum):
    PENDING      = "pending"
    EXTRACTING   = "extracting"    # ffmpeg audio extraction
    SEPARATING   = "separating"    # vocal / BGM split (HF Space)
    STEMS_READY  = "stems_ready"   # paused — waiting for user to click Analyze
    DIARIZING    = "diarizing"     # speaker diarization
    TRANSCRIBING = "transcribing"  # ASR
    TRANSLATING  = "translating"   # translation
    SYNTHESIZING = "synthesizing"  # TTS
    MIXING       = "mixing"        # final audio mix
    COMPLETED    = "completed"
    FAILED       = "failed"


class Gender(str, enum.Enum):
    MALE    = "male"
    FEMALE  = "female"
    UNKNOWN = "unknown"


class AgeGroup(str, enum.Enum):
    CHILD  = "child"
    YOUNG  = "young"
    ADULT  = "adult"
    SENIOR = "senior"


class VoiceMode(str, enum.Enum):
    DESIGN     = "design"      # description only, no reference audio
    CLONE      = "clone"       # reference audio + optional control/style
    ULTIMATE   = "ultimate"    # reference audio + transcript (audio continuation)


# ── Voice (workspace-global reusable voice library) ───────────
class Voice(Base):
    """
    A named, reusable voice preset created in the Voice Creator.
    Workspace-global (not tied to a project) so it can be selected across
    any project's segments/speakers.
    """
    __tablename__ = "voices"

    id          = Column(String, primary_key=True, default=generate_uuid)
    name        = Column(String(120), nullable=False)
    # Stored as plain string (validated by the VoiceMode enum in the schema
    # layer) to avoid native-enum/VARCHAR mismatches in Postgres.
    mode        = Column(String(20), default=VoiceMode.DESIGN.value)

    # Voice Design description / cloning control instruction (the "(...)" prompt)
    description = Column(Text, default="")
    # Reference clip for cloning modes (local path under uploads/voices/)
    reference_audio_path = Column(String, default="")
    # Transcript of the reference clip (ultimate cloning)
    reference_transcript = Column(Text, default="")

    # VoxCPM2 generation params
    cfg_value           = Column(Float, default=2.0)
    inference_timesteps = Column(Integer, default=10)
    # Fixed seed so the voice identity is consistent across every line.
    # -1 means "not frozen" (random each call); a concrete value locks the voice.
    seed                = Column(Integer, default=-1)

    created_at  = Column(DateTime, server_default=func.now())
    updated_at  = Column(DateTime, server_default=func.now(), onupdate=func.now())


# ── Project ───────────────────────────────────────────────────
class Project(Base):
    __tablename__ = "projects"

    id          = Column(String, primary_key=True, default=generate_uuid)
    name        = Column(String(255), nullable=False)
    description = Column(Text, default="")
    source_lang = Column(String(10), default="zh")   # ISO code
    target_lang = Column(String(10), default="km")   # Khmer
    logo_path   = Column(String, default="")         # watermark, reused across exports
    created_at  = Column(DateTime, server_default=func.now())
    updated_at  = Column(DateTime, server_default=func.now(), onupdate=func.now())

    jobs     = relationship("Job", back_populates="project", cascade="all, delete")
    speakers = relationship("Speaker", back_populates="project", cascade="all, delete")


# ── Job ───────────────────────────────────────────────────────
class Job(Base):
    __tablename__ = "jobs"

    id          = Column(String, primary_key=True, default=generate_uuid)
    project_id  = Column(String, ForeignKey("projects.id"), nullable=False)
    status      = Column(SAEnum(JobStatus), default=JobStatus.PENDING)
    progress    = Column(Integer, default=0)          # 0-100
    error_msg   = Column(Text, default="")

    # File paths (stored on disk / S3)
    video_path    = Column(String, default="")
    audio_path    = Column(String, default="")
    output_path   = Column(String, default="")        # final dubbed video
    subtitle_path = Column(String, default="")         # set for subtitle-pipeline jobs

    # Timing
    created_at    = Column(DateTime, server_default=func.now())
    completed_at  = Column(DateTime, nullable=True)
    duration_secs = Column(Float, default=0.0)        # video duration

    project  = relationship("Project", back_populates="jobs")
    segments = relationship("Segment", back_populates="job", cascade="all, delete",
                            order_by="Segment.start_time")
    overlays = relationship("Overlay", back_populates="job", cascade="all, delete",
                            order_by="Overlay.z_index")


# ── Speaker ───────────────────────────────────────────────────
class Speaker(Base):
    __tablename__ = "speakers"

    id          = Column(String, primary_key=True, default=generate_uuid)
    project_id  = Column(String, ForeignKey("projects.id"), nullable=False)
    label       = Column(String(50), nullable=False)   # e.g. "SPEAKER_00"
    display_name = Column(String(100), default="")     # e.g. "Actor 1"
    gender      = Column(SAEnum(Gender), default=Gender.UNKNOWN)
    age_group   = Column(SAEnum(AgeGroup), default=AgeGroup.ADULT)

    # VoxCPM2 voice design prompt (auto-generated or manually edited; legacy
    # fallback used when no Voice is assigned)
    voice_design_prompt = Column(Text, default="")
    # Reference audio clip for voice cloning (path to .wav file)
    reference_audio_path = Column(String, default="")

    # Speaker-level voice assignment — applies to all of this speaker's segments
    # unless a segment overrides it. FK to the workspace voice library.
    voice_id = Column(String, ForeignKey("voices.id"), nullable=True)

    project  = relationship("Project", back_populates="speakers")
    segments = relationship("Segment", back_populates="speaker")


# ── Segment ───────────────────────────────────────────────────
class Segment(Base):
    __tablename__ = "segments"

    id         = Column(String, primary_key=True, default=generate_uuid)
    job_id     = Column(String, ForeignKey("jobs.id"), nullable=False)
    speaker_id = Column(String, ForeignKey("speakers.id"), nullable=True)

    # Vertical timeline row — independent of speaker_id, so clips from any
    # speaker can share a lane or be spread across separate ones.
    lane_index = Column(Integer, nullable=True, default=0)

    # Timing
    start_time = Column(Float, nullable=False)   # seconds
    end_time   = Column(Float, nullable=False)   # seconds

    # Text content
    source_text  = Column(Text, default="")     # original Chinese
    english_text = Column(Text, default="")     # ZH → EN
    khmer_text   = Column(Text, default="")     # ZH → KM

    # TTS output
    tts_audio_path    = Column(String, default="")   # synthesised .wav
    tts_duration_secs = Column(Float, default=0.0)

    # Per-segment voice override — takes precedence over the speaker's voice.
    voice_id = Column(String, ForeignKey("voices.id"), nullable=True)

    # Audio effects parameters
    volume_db    = Column(Float, default=0.0)
    voice_filter = Column(String(50), default="")
    voice_speed  = Column(Float, default=1.0)

    # Review flags
    is_approved  = Column(Boolean, default=False)
    notes        = Column(Text, default="")

    job     = relationship("Job", back_populates="segments")
    speaker = relationship("Speaker", back_populates="segments")


# ── Overlay ───────────────────────────────────────────────────
# A positioned layer on the video canvas — either a dropped image (logo,
# sticker, etc.) or the burned-in subtitle box. Position/size are stored as
# fractions of the video's own width/height (0-1), so the same values map
# identically onto the live editor preview and the full-resolution export.
# ── Overlay template (workspace-global "brand kit") ───────────
class OverlayTemplate(Base):
    """
    A saved snapshot of one job's full overlay layout (logo, boxes, subtitle
    style), reusable across every episode. Workspace-global like Voice, since
    episodes live in separate projects. `items` holds the overlay dicts
    (same fields as Overlay minus id/job_id); image items' media_path points
    into uploads/templates/<template_id>/, owned by the template — applying
    copies the file into the target job so deleting either side is safe.
    Positions are fractions of video dimensions, so a template maps
    identically onto every (vertical) episode.
    """
    __tablename__ = "overlay_templates"

    id         = Column(String, primary_key=True, default=generate_uuid)
    name       = Column(String(120), nullable=False)
    is_default = Column(Boolean, default=False)  # auto-applied to newly uploaded episodes
    items      = Column(JSON, default=list)
    created_at = Column(DateTime, server_default=func.now())


class Overlay(Base):
    __tablename__ = "overlays"

    id         = Column(String, primary_key=True, default=generate_uuid)
    job_id     = Column(String, ForeignKey("jobs.id"), nullable=False)
    type       = Column(String(20), default="image")   # "image" | "subtitle" | "shape"
    media_path = Column(String, default="")             # image overlays only

    # Shape overlays only — a plain box used to cover something in the
    # source video (e.g. a burned-in original-language subtitle) so a new
    # subtitle overlay reads cleanly on top of it. `color`/`opacity` above
    # double as the shape's fill when not blurring.
    blur = Column(Boolean, default=False)   # True = blur the video underneath instead of filling with color

    # Box — top-left corner + size, all fractions of video width/height.
    x      = Column(Float, default=0.7)
    y      = Column(Float, default=0.05)
    width  = Column(Float, default=0.2)
    height = Column(Float, default=0.15)

    opacity = Column(Float, default=1.0)
    z_index = Column(Integer, default=0)

    # Visibility window — null on both means visible for the whole video.
    start_time = Column(Float, nullable=True)
    end_time   = Column(Float, nullable=True)

    # Subtitle overlays only.
    font_size        = Column(Integer, default=42)
    color            = Column(String(20), default="white")   # text fill
    outline_color    = Column(String(20), default="black")   # text stroke
    background_color = Column(String(20), default="")        # box fill behind text; "" = none

    created_at = Column(DateTime, server_default=func.now())

    job = relationship("Job", back_populates="overlays")
