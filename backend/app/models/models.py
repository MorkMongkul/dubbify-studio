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
from datetime import datetime, timedelta, timezone
import enum
import uuid


def generate_uuid():
    return str(uuid.uuid4())


_last_message_time = datetime.min


def message_time() -> datetime:
    """
    A strictly increasing UTC time for each Assistant message. The database's
    own clock only has whole seconds, and a tool call and its result are saved
    within the same second; the history is replayed to Gemini in this order.
    """
    global _last_message_time
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if now <= _last_message_time:
        now = _last_message_time + timedelta(microseconds=1)
    _last_message_time = now
    return now


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

    # Tags for finding a voice (app/services/voice_tags.py): who it sounds
    # like and roughly how old, which together pick its section in the Voices
    # tab, plus free personality / role tags. NULL on voices made before tags.
    gender      = Column(String(10), nullable=True)      # man / woman / boy / girl
    age_group   = Column(String(10), nullable=True)      # child, teen, 20s … 70s+
    personality = Column(JSON, default=list)
    roles       = Column(JSON, default=list)

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

    # Assistant chat: how ChatProposedEdit.propose_edit tool calls are handled.
    # "manual" (default) — a proposed edit is staged (status="pending") and
    #   waits for the user to approve/reject it in the Assistant panel; the
    #   segment's khmer_text is untouched until then.
    # "auto"   — the edit applies immediately AND is still logged as a
    #   status="approved" row, so there's an auditable history / manual
    #   revert path even when nothing waited for a human click.
    chat_edit_mode = Column(String(10), default="manual")

    # Story notes (app/services/story_notes.py): the user's intro plus what the
    # AI learned from finished episodes, used by translation and the Assistant.
    # NULL = empty notes.
    story_notes = Column(JSON, nullable=True)

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

    # User-facing session label (e.g. "Episode 5"). Every uploaded video is
    # stored on disk as a generic source.mp4 regardless of its original
    # filename, so once a project holds more than one session, the Sessions
    # panel has nothing else distinctive to show — this is what it displays
    # instead, falling back to the filename when blank (the common case for
    # a single-session project, where the project's own name already IDs it).
    title = Column(String(255), default="")

    # File paths (stored on disk / S3)
    video_path    = Column(String, default="")
    audio_path    = Column(String, default="")
    output_path   = Column(String, default="")        # final dubbed video
    subtitle_path = Column(String, default="")         # set for subtitle-pipeline jobs

    # Timing
    created_at    = Column(DateTime, server_default=func.now())
    completed_at  = Column(DateTime, nullable=True)
    # When the user marked the episode finished (its translation is right), so
    # its story notes can be learned from it; NULL = not finished
    finished_at   = Column(DateTime, nullable=True)
    duration_secs = Column(Float, default=0.0)        # video duration

    # BGM/SFX stem gain — baked into the no_vocals stem on disk (see
    # routes/jobs.py:update_bgm_volume), so this column just mirrors the
    # currently-applied value for the UI slider to read back.
    bgm_volume_db = Column(Float, default=0.0)

    # Timeline rules the episode's clips follow: 1 = 0.3.0 (a clip is as long as
    # its voice). NULL = made before 0.3.0, converted once at startup
    # (timeline.convert_old_jobs). New jobs get 1.
    timeline_version = Column(Integer, nullable=True, default=1)
    # The video's frame rate (one ← / → step in the editor); NULL = not read yet.
    frame_rate = Column(Float, nullable=True)
    # The music & effects dip under the voices (timeline.duck_spans). NULL =
    # analysed before 0.3.1: off, so its export doesn't change; analysis sets it.
    duck_music = Column(Boolean, nullable=True)

    project  = relationship("Project", back_populates="jobs")
    segments = relationship("Segment", back_populates="job", cascade="all, delete",
                            order_by="Segment.start_time")
    overlays = relationship("Overlay", back_populates="job", cascade="all, delete",
                            order_by="Overlay.z_index")
    stem_pieces = relationship("StemPiece", back_populates="job", cascade="all, delete",
                               order_by="StemPiece.start_time")


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
    # True when the app picked voice_id itself (app/services/auto_voice.py)
    # because nobody chose one; False once the user chooses. A picked voice
    # doesn't make the speaker "confirmed" (pipeline_common.is_confirmed_speaker).
    voice_auto = Column(Boolean, default=False)

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

    # Timing: where the clip is on the timeline. A clip with a voice is exactly
    # as long as the part of the voice it plays (app/services/timeline.py).
    start_time = Column(Float, nullable=False)   # seconds
    end_time   = Column(Float, nullable=False)   # seconds
    # Where the actor spoke in the original (a fact about the video: stays put
    # when the clip moves). NULL for a line added by hand.
    source_start = Column(Float, nullable=True)
    source_end   = Column(Float, nullable=True)

    # Text content
    source_text  = Column(Text, default="")     # original Chinese
    english_text = Column(Text, default="")     # ZH → EN
    khmer_text   = Column(Text, default="")     # ZH → KM

    # TTS output
    tts_audio_path    = Column(String, default="")   # synthesised .wav
    tts_duration_secs = Column(Float, default=0.0)
    # The clip plays its voice file from voice_offset (seconds into the file)
    # for (end_time - start_time) seconds at fit_rate on top of the speed baked
    # into the file. NULL (rows from before 0.3.0) reads as 0.0 / 1.0.
    voice_offset = Column(Float, nullable=True, default=0.0)
    fit_rate     = Column(Float, nullable=True, default=1.0)
    # The Khmer text the voice was made from; differs from khmer_text once the
    # text is edited after the voice was made (the voice is kept).
    voice_text   = Column(Text, nullable=True)
    # Linear fades on the clip as trimmed, in seconds of timeline (0.3.1);
    # NULL (rows from before 0.3.1) reads as 0 — no fade.
    fade_in_secs  = Column(Float, nullable=True, default=0.0)
    fade_out_secs = Column(Float, nullable=True, default=0.0)

    # Per-segment voice override — takes precedence over the speaker's voice.
    voice_id = Column(String, ForeignKey("voices.id"), nullable=True)

    # Audio effects parameters
    volume_db    = Column(Float, default=0.0)
    voice_filter = Column(String(50), default="")
    voice_speed  = Column(Float, default=1.0)
    # The gain that gave the voice the original line's loudness, decided when the
    # voice was made (voice_level.level_new_voice). NULL: made before 0.2.8, left as it was.
    level_gain_db = Column(Float, nullable=True)

    # Review flags
    is_approved  = Column(Boolean, default=False)
    notes        = Column(Text, default="")

    job     = relationship("Job", back_populates="segments")
    speaker = relationship("Speaker", back_populates="segments")


# ── Stem pieces (0.3.2) ───────────────────────────────────────
class StemPiece(Base):
    """
    A piece of the music & effects (stem "music") or of the original voices
    (stem "voices"): the part of that stem heard from start_time to end_time,
    at the same time as in the video (pieces never move — locked to the
    picture), with its own volume and fades. Between pieces: silence. An
    episode starts with one whole piece per stem (app/services/stem_pieces.py),
    so it sounds as before. The original voices are only in the export where a
    piece is switched on (in_export); the music always is.
    """
    __tablename__ = "stem_pieces"

    id            = Column(String, primary_key=True, default=generate_uuid)
    job_id        = Column(String, ForeignKey("jobs.id"), nullable=False, index=True)
    stem          = Column(String(10), nullable=False)
    start_time    = Column(Float, nullable=False)
    end_time      = Column(Float, nullable=False)
    volume_db     = Column(Float, default=0.0)
    fade_in_secs  = Column(Float, default=0.0)
    fade_out_secs = Column(Float, default=0.0)
    in_export     = Column(Boolean, default=False)

    job = relationship("Job", back_populates="stem_pieces")


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


# ── Assistant chat (project-scoped translation memory chatbot) ────────────
class ChatThread(Base):
    """
    One Assistant chat of a project. A project has several; each sends only
    its own messages to Gemini as history. Named after its first message
    (chat_agent.thread_title). Messages saved before chats had threads become
    one chat the first time the project's chats are listed
    (chat_agent.adopt_unthreaded).
    """
    __tablename__ = "chat_threads"

    id         = Column(String, primary_key=True, default=generate_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    title      = Column(String(120), default="")
    created_at = Column(DateTime, default=message_time)
    updated_at = Column(DateTime, default=message_time)   # the last message's time; the History list's order


class ChatMessage(Base):
    """
    One turn of a project's single ongoing Assistant conversation. Stored
    locally (not relying on Gemini's server-side interaction state) so the
    transcript survives independent of any one API's retention policy and
    replays deterministically into the next generateContent call — see
    app/services/chat_agent.py for how these get turned back into Gemini's
    `contents` array.

    role="user" covers both real user messages (function_response is null)
    and the function-result turn fed back after a tool call (function_response
    set) — that's the role Gemini's REST API expects a function response to
    carry. role="model" covers both a plain text reply (function_call null)
    and a tool-call request (function_call set).
    """
    __tablename__ = "chat_messages"

    id         = Column(String, primary_key=True, default=generate_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    thread_id  = Column(String, ForeignKey("chat_threads.id"), nullable=True)   # NULL: from before threads
    role       = Column(String(10), nullable=False)   # "user" | "model"
    text       = Column(Text, default="")               # empty for pure function-call/response turns

    function_call     = Column(JSON, nullable=True)   # {"name": ..., "args": {...}}
    function_response = Column(JSON, nullable=True)   # {"name": ..., "response": {...}}
    # The line the user's text cursor was in when they sent this message
    # ({segment_id, episode, time, speaker, speaker_tag, source_text,
    # khmer_text}, a snapshot), so "this line" means the same on every replay.
    line_ref = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=message_time, server_default=func.now())


class ChatProposedEdit(Base):
    """
    A segment translation change proposed by the Assistant's propose_edit
    tool. In "manual" mode (Project.chat_edit_mode) this row is the ONLY
    effect of the tool call — status stays "pending" until the user approves
    or rejects it in the Assistant panel, and the segment is untouched until
    then. In "auto" mode the segment is updated immediately and the row is
    written already status="approved", purely as an audit / revert trail.
    """
    __tablename__ = "chat_proposed_edits"

    id         = Column(String, primary_key=True, default=generate_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    segment_id = Column(String, ForeignKey("segments.id"), nullable=False)
    message_id = Column(String, ForeignKey("chat_messages.id"), nullable=True)

    old_khmer_text = Column(Text, default="")
    new_khmer_text = Column(Text, default="")
    reason         = Column(Text, default="")
    status         = Column(String(10), default="pending")  # "pending" | "approved" | "rejected"

    created_at  = Column(DateTime, server_default=func.now())
    resolved_at = Column(DateTime, nullable=True)
