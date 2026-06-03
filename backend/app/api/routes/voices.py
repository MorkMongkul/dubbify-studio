"""
app/api/routes/voices.py
Voice library (Voice Creator) — workspace-global, reusable named voices.

A Voice is one of three VoxCPM2 modes:
  - design   : description only (no reference audio)
  - clone    : reference audio + optional control/style description
  - ultimate : reference audio + its transcript (audio continuation)

Created once here, then selected per speaker / per segment in the editor.
"""
import shutil
import random
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from typing import List, Optional

from app.core.database import get_db
from app.core.config import settings
from app.models.models import Voice, VoiceMode, Segment, Speaker
from app.schemas.schemas import VoiceResponse, VoiceUpdate, VoicePreviewRequest
from app.services.tts_client import tts_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voices", tags=["Voice Library"])

VOICES_DIR = Path(settings.UPLOAD_DIR) / "voices"
_REF_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".ogg"}

# Neutral Khmer line used to bake a design voice into a reference clip.
BAKE_TEXT = "សួស្តី ខ្ញុំជាសំឡេងដែលបង្កើតឡើងសម្រាប់ការដាប់ភាសាខ្មែរ។"


async def _bake_design_voice(voice: Voice, db: AsyncSession) -> bool:
    """
    Generate ONE reference clip from a design voice (description + seed) and
    store it on the voice. All later synthesis then CLONES from this clip, so
    every line of the voice has an identical timbre — voice design alone is not
    consistent across different text, but cloning from a fixed reference is.
    """
    voice_dir = VOICES_DIR / voice.id
    voice_dir.mkdir(parents=True, exist_ok=True)
    ref_path = voice_dir / "reference.wav"
    result = await tts_client.synthesize(
        text=BAKE_TEXT,
        voice_design=voice.description,
        output_path=str(ref_path),
        cfg_value=voice.cfg_value,
        inference_timesteps=voice.inference_timesteps,
        seed=voice.seed,
        reference_audio_path="",   # force pure design generation (no cloning)
    )
    if result["success"]:
        voice.reference_audio_path = str(ref_path)
        voice.reference_transcript = BAKE_TEXT
        await db.flush()
        logger.info(f"Baked reference for design voice {voice.name} ({voice.id[:8]})")
        return True
    logger.warning(f"Bake failed for voice {voice.id[:8]}: {result.get('error')}")
    return False


def _save_reference(voice_id: str, file: UploadFile) -> str:
    suffix = Path(file.filename or "ref.wav").suffix.lower()
    if suffix not in _REF_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio type '{suffix}'. Allowed: {sorted(_REF_EXTS)}",
        )
    dest_dir = VOICES_DIR / voice_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"reference{suffix}"
    with open(dest, "wb") as out:
        shutil.copyfileobj(file.file, out)
    return str(dest)


@router.get("/", response_model=List[VoiceResponse])
async def list_voices(db: AsyncSession = Depends(get_db)):
    """List all voices in the workspace library."""
    result = await db.execute(select(Voice).order_by(Voice.created_at.desc()))
    return result.scalars().all()


@router.post("/", response_model=VoiceResponse, status_code=status.HTTP_201_CREATED)
async def create_voice(
    name: str = Form(...),
    mode: VoiceMode = Form(VoiceMode.DESIGN),
    description: str = Form(""),
    reference_transcript: str = Form(""),
    cfg_value: float = Form(2.0),
    inference_timesteps: int = Form(10),
    seed: int = Form(-1),
    reference_audio: Optional[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a named voice. Send multipart/form-data; include `reference_audio`
    for clone/ultimate modes.
    """
    if mode in (VoiceMode.CLONE, VoiceMode.ULTIMATE) and reference_audio is None:
        raise HTTPException(
            status_code=400,
            detail=f"Mode '{mode.value}' requires a reference_audio file.",
        )
    if mode == VoiceMode.ULTIMATE and not reference_transcript.strip():
        raise HTTPException(
            status_code=400,
            detail="Ultimate cloning requires reference_transcript.",
        )

    # Freeze the voice to a concrete seed so every line is the SAME voice.
    # Design mode especially needs this — otherwise VoxCPM2 samples a new
    # voice per generation. A caller can pass an explicit seed to reproduce one.
    if seed is None or seed < 0:
        seed = random.randint(1, 2_000_000_000)

    voice = Voice(
        name=name,
        mode=mode.value,
        description=description,
        reference_transcript=reference_transcript,
        cfg_value=cfg_value,
        inference_timesteps=inference_timesteps,
        seed=seed,
    )
    db.add(voice)
    await db.flush()  # assigns voice.id

    if reference_audio is not None:
        voice.reference_audio_path = _save_reference(voice.id, reference_audio)
    elif mode == VoiceMode.DESIGN:
        # Freeze the design into a reference clip so all lines clone the same
        # voice. If the TTS host is unreachable, the voice is still created and
        # falls back to (less consistent) design generation until re-baked.
        await _bake_design_voice(voice, db)

    await db.commit()
    await db.refresh(voice)
    return voice


@router.patch("/{voice_id}", response_model=VoiceResponse)
async def update_voice(
    voice_id: str, payload: VoiceUpdate, db: AsyncSession = Depends(get_db)
):
    """Update a voice's metadata (name, description, transcript, params, mode)."""
    result = await db.execute(select(Voice).where(Voice.id == voice_id))
    voice = result.scalar_one_or_none()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")

    data = payload.model_dump(exclude_none=True)
    if "mode" in data:
        data["mode"] = data["mode"].value  # enum -> stored string
    identity_changed = any(
        k in data for k in ("seed", "description", "cfg_value", "inference_timesteps", "mode")
    )
    for field, value in data.items():
        setattr(voice, field, value)

    # Re-bake a design voice when its identity params change (e.g. reroll seed)
    # so the stored reference matches the new voice.
    if identity_changed and voice.mode == VoiceMode.DESIGN.value:
        await _bake_design_voice(voice, db)

    await db.flush()
    await db.refresh(voice)
    return voice


@router.post("/{voice_id}/reference", response_model=VoiceResponse)
async def upload_reference(
    voice_id: str,
    reference_audio: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload or replace a voice's reference audio clip."""
    result = await db.execute(select(Voice).where(Voice.id == voice_id))
    voice = result.scalar_one_or_none()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")

    voice.reference_audio_path = _save_reference(voice.id, reference_audio)
    await db.flush()
    await db.refresh(voice)
    return voice


@router.delete("/{voice_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_voice(voice_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a voice and its reference audio."""
    result = await db.execute(select(Voice).where(Voice.id == voice_id))
    voice = result.scalar_one_or_none()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")

    # Clear references first so the FK constraint doesn't block the delete.
    # Segments/speakers using this voice fall back to the speaker prompt / default.
    await db.execute(update(Segment).where(Segment.voice_id == voice_id).values(voice_id=None))
    await db.execute(update(Speaker).where(Speaker.voice_id == voice_id).values(voice_id=None))

    voice_dir = VOICES_DIR / voice_id
    if voice_dir.exists():
        shutil.rmtree(voice_dir, ignore_errors=True)
    await db.delete(voice)
    await db.commit()


@router.post("/{voice_id}/preview")
async def preview_voice(
    voice_id: str, payload: VoicePreviewRequest, db: AsyncSession = Depends(get_db)
):
    """
    Generate a short sample with this voice and return the WAV directly.
    Used by the Voice Creator's preview button.
    """
    result = await db.execute(select(Voice).where(Voice.id == voice_id))
    voice = result.scalar_one_or_none()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")

    preview_dir = VOICES_DIR / voice_id
    preview_dir.mkdir(parents=True, exist_ok=True)
    out_path = preview_dir / "preview.wav"

    # Reference-driven (matches the editor): clone from the stored/baked
    # reference when present so the preview is the same voice the editor uses.
    has_ref = bool(voice.reference_audio_path)
    has_transcript = has_ref and bool(voice.reference_transcript)
    result_data = await tts_client.synthesize(
        text=payload.text,
        voice_design="" if has_transcript else voice.description,
        output_path=str(out_path),
        cfg_value=voice.cfg_value,
        inference_timesteps=voice.inference_timesteps,
        reference_audio_path=voice.reference_audio_path or "",
        reference_transcript=voice.reference_transcript if has_transcript else "",
        seed=voice.seed,
    )

    if not result_data["success"]:
        raise HTTPException(status_code=500, detail=result_data.get("error", "Preview failed"))

    return Response(content=Path(out_path).read_bytes(), media_type="audio/wav")
