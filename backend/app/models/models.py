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
    PENDING    = "pending"
    EXTRACTING = "extracting"    # ffmpeg audio extraction
    DIARIZING  = "diarizing"     # speaker diarization
    TRANSCRIBING = "transcribing"  # Whisper ASR
    TRANSLATING  = "translating"   # NLLB-200
    SYNTHESIZING = "synthesizing"  # VoxCPM2 TTS
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


# ── Project ───────────────────────────────────────────────────
class Project(Base):
    __tablename__ = "projects"

    id          = Column(String, primary_key=True, default=generate_uuid)
    name        = Column(String(255), nullable=False)
    description = Column(Text, default="")
    source_lang = Column(String(10), default="zh")   # ISO code
    target_lang = Column(String(10), default="km")   # Khmer
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
    video_path  = Column(String, default="")
    audio_path  = Column(String, default="")
    output_path = Column(String, default="")          # final dubbed video

    # Timing
    created_at    = Column(DateTime, server_default=func.now())
    completed_at  = Column(DateTime, nullable=True)
    duration_secs = Column(Float, default=0.0)        # video duration

    project  = relationship("Project", back_populates="jobs")
    segments = relationship("Segment", back_populates="job", cascade="all, delete",
                            order_by="Segment.start_time")


# ── Speaker ───────────────────────────────────────────────────
class Speaker(Base):
    __tablename__ = "speakers"

    id          = Column(String, primary_key=True, default=generate_uuid)
    project_id  = Column(String, ForeignKey("projects.id"), nullable=False)
    label       = Column(String(50), nullable=False)   # e.g. "SPEAKER_00"
    display_name = Column(String(100), default="")     # e.g. "Actor 1"
    gender      = Column(SAEnum(Gender), default=Gender.UNKNOWN)
    age_group   = Column(SAEnum(AgeGroup), default=AgeGroup.ADULT)

    # VoxCPM2 voice design prompt (auto-generated or manually edited)
    voice_design_prompt = Column(Text, default="")
    # Reference audio clip for voice cloning (path to .wav file)
    reference_audio_path = Column(String, default="")

    project  = relationship("Project", back_populates="speakers")
    segments = relationship("Segment", back_populates="speaker")


# ── Segment ───────────────────────────────────────────────────
class Segment(Base):
    __tablename__ = "segments"

    id         = Column(String, primary_key=True, default=generate_uuid)
    job_id     = Column(String, ForeignKey("jobs.id"), nullable=False)
    speaker_id = Column(String, ForeignKey("speakers.id"), nullable=True)

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

    # Review flags
    is_approved  = Column(Boolean, default=False)
    notes        = Column(Text, default="")

    job     = relationship("Job", back_populates="segments")
    speaker = relationship("Speaker", back_populates="segments")
