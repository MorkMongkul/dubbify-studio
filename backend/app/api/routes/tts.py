"""
app/api/routes/tts.py
TTS synthesis endpoints — trigger VoxCPM2 for individual segments or entire jobs.
"""
import asyncio
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from pydantic import BaseModel
 
from app.core.database import get_db
from app.core.config import settings
from app.models.models import Segment, Speaker, Job, JobStatus, Voice, VoiceMode
from app.schemas.schemas import TTSResponse
from app.services.tts_client import tts_client
from app.services.audio_extractor import mix_dubbed_audio

router = APIRouter(prefix="/tts", tags=["TTS Synthesis"])


async def _resolve_voice(seg: Segment, db: AsyncSession) -> dict:
    """
    Resolve the synthesis parameters for a segment, in priority order:
      1. segment.voice_id        (per-segment override)
      2. speaker.voice_id        (speaker-level assignment)
      3. speaker.voice_design_prompt  (legacy fallback, design-only)

    Returns kwargs for tts_client.synthesize().
    """
    async def _voice_kwargs(voice: Voice) -> dict:
        # Reference-driven: if the voice has a reference clip (uploaded for
        # clone/ultimate, OR auto-baked from a design profile), clone from it so
        # the timbre is identical across every line. A transcript → ultimate
        # cloning; otherwise controllable cloning with the description as style.
        has_ref = bool(voice.reference_audio_path)
        has_transcript = has_ref and bool(voice.reference_transcript)
        return {
            # In ultimate cloning the style prompt is ignored, so drop it there.
            "voice_design": "" if has_transcript else voice.description,
            "cfg_value": voice.cfg_value,
            "inference_timesteps": voice.inference_timesteps,
            "reference_audio_path": voice.reference_audio_path or "",
            "reference_transcript": voice.reference_transcript if has_transcript else "",
            "seed": voice.seed,  # fixed seed → consistent voice across all lines
        }

    # 1. Per-segment override
    if seg.voice_id:
        v = (await db.execute(select(Voice).where(Voice.id == seg.voice_id))).scalar_one_or_none()
        if v:
            return await _voice_kwargs(v)

    # 2 & 3. Speaker-level voice, then legacy design prompt
    if seg.speaker_id:
        speaker = (await db.execute(select(Speaker).where(Speaker.id == seg.speaker_id))).scalar_one_or_none()
        if speaker:
            if speaker.voice_id:
                v = (await db.execute(select(Voice).where(Voice.id == speaker.voice_id))).scalar_one_or_none()
                if v:
                    return await _voice_kwargs(v)
            return {"voice_design": speaker.voice_design_prompt or ""}

    return {"voice_design": ""}
 
 
async def _synthesize_segment_db(segment_id: str, db: AsyncSession) -> Segment:
    result = await db.execute(select(Segment).where(Segment.id == segment_id))
    seg = result.scalar_one_or_none()
    if not seg:
        raise HTTPException(status_code=404, detail="Segment not found")
 
    if not seg.khmer_text or not seg.khmer_text.strip():
        raise HTTPException(status_code=400, detail="Segment has no Khmer text to synthesize")

    # Resolve the assigned voice (segment override → speaker voice → legacy prompt)
    voice_kwargs = await _resolve_voice(seg, db)

    # Resolve project_id for correct upload path
    j_result = await db.execute(select(Job).where(Job.id == seg.job_id))
    job = j_result.scalar_one_or_none()
    project_id = job.project_id if job else seg.job_id

    # Build output path: uploads/{project_id}/{job_id}/tts/seg_{id}.wav
    job_dir = Path(settings.UPLOAD_DIR) / project_id / seg.job_id
    tts_dir = job_dir / "tts"
    tts_dir.mkdir(parents=True, exist_ok=True)
    output_raw_path = tts_dir / f"seg_{segment_id}_raw.wav"
    output_final_path = tts_dir / f"seg_{segment_id}.wav"

    # Call VoxCPM2 or Gemini TTS
    result_data = await tts_client.synthesize(
        text=seg.khmer_text,
        output_path=str(output_raw_path),
        **voice_kwargs,
    )
 
    if result_data["success"]:
        from app.services.audio_extractor import apply_audio_effects
        new_duration = await apply_audio_effects(
            input_path=str(output_raw_path),
            output_path=str(output_final_path),
            volume_db=seg.volume_db,
            voice_filter=seg.voice_filter,
            voice_speed=seg.voice_speed,
        )
        seg.tts_audio_path    = str(output_final_path)
        seg.tts_duration_secs = new_duration
        await db.flush()
        return seg
    else:
        raise HTTPException(status_code=500, detail=result_data.get("error", "TTS synthesis failed"))
 
 
@router.post("/synthesize/segment/{segment_id}", response_model=TTSResponse)
async def synthesize_segment(
    segment_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Synthesize a single segment.
    """
    seg = await _synthesize_segment_db(segment_id, db)
    await db.commit()
    return TTSResponse(
        segment_id=segment_id,
        audio_path=seg.tts_audio_path,
        duration_secs=seg.tts_duration_secs,
        success=True,
        error="",
    )
 
 
class BatchSynthesizeRequest(BaseModel):
    segment_ids: List[str]
 
 
@router.post("/synthesize/batch")
async def synthesize_batch(
    payload: BatchSynthesizeRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Synthesize multiple segments sequentially with a pacing delay
    to prevent Gemini API rate limit errors (429).
    """
    results = []
    for i, segment_id in enumerate(payload.segment_ids):
        try:
            seg = await _synthesize_segment_db(segment_id, db)
            results.append({
                "segment_id": segment_id,
                "audio_path": seg.tts_audio_path,
                "duration_secs": seg.tts_duration_secs,
                "success": True,
                "error": ""
            })
        except HTTPException as e:
            results.append({
                "segment_id": segment_id,
                "audio_path": "",
                "duration_secs": 0,
                "success": False,
                "error": e.detail
            })
        except Exception as e:
            results.append({
                "segment_id": segment_id,
                "audio_path": "",
                "duration_secs": 0,
                "success": False,
                "error": str(e)
            })
        
        # Add 1.5 seconds delay between generations for batch requests (except the last one)
        if i < len(payload.segment_ids) - 1:
            await asyncio.sleep(1.5)
 
    await db.commit()
    return {"results": results}


@router.post("/synthesize/job/{job_id}")
async def synthesize_job(
    job_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Synthesize ALL approved segments in a job (runs in background).
    Only processes segments where is_approved=True.
    """
    result = await db.execute(
        select(Segment)
        .where(Segment.job_id == job_id, Segment.is_approved == True)
        .order_by(Segment.start_time)
    )
    segments = result.scalars().all()

    if not segments:
        raise HTTPException(
            status_code=400,
            detail="No approved segments found. Approve segments first.",
        )

    background_tasks.add_task(_synthesize_all_segments, job_id)
    await db.commit()

    return {
        "message": f"TTS synthesis started for {len(segments)} approved segments.",
        "job_id": job_id,
        "segment_count": len(segments),
    }


@router.post("/mix/{job_id}")
async def mix_final_audio(
    job_id: str,
    mute_original: bool = True,
    db: AsyncSession = Depends(get_db),
):
    """
    Mix all synthesized TTS segments back into the original video.
    Produces the final dubbed .mp4 file.
    """
    # Get job
    j_result = await db.execute(select(Job).where(Job.id == job_id))
    job = j_result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Get segments that have TTS audio
    result = await db.execute(
        select(Segment)
        .where(Segment.job_id == job_id, Segment.tts_audio_path != "")
        .order_by(Segment.start_time)
    )
    segments = result.scalars().all()

    if not segments:
        raise HTTPException(status_code=400, detail="No TTS audio found. Run synthesis first.")

    tts_seg_list = [
        {
            "start_time": seg.start_time,
            "audio_path": seg.tts_audio_path,
            "duration": seg.tts_duration_secs,
        }
        for seg in segments
    ]

    job_dir = Path(settings.UPLOAD_DIR) / job.project_id / job_id
    output_path = str(job_dir / "dubbed_output.mp4")

    # Use separated BGM track when available — gives clean BGM + TTS with no voice bleed-through
    bgm_wav = job_dir / "no_vocals.wav"

    try:
        final_path = await mix_dubbed_audio(
            video_path=job.video_path,
            tts_segments=tts_seg_list,
            output_path=output_path,
            mute_original=mute_original,
            bgm_path=str(bgm_wav) if bgm_wav.exists() else None,
        )
        job.output_path = final_path
        job.status = JobStatus.COMPLETED
        await db.flush()

        return {
            "success": True,
            "output_path": final_path,
            "segments_mixed": len(segments),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Audio mixing failed: {e}")


async def _synthesize_all_segments(job_id: str):
    """Background task: synthesize all segments with speaker voice profiles."""
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Segment)
            .where(Segment.job_id == job_id, Segment.is_approved == True)
            .order_by(Segment.start_time)
        )
        segments = result.scalars().all()

        if not segments:
            logger.warning(f"No approved segments found for background synthesis of job {job_id}.")
            return

        # Resolve project_id for correct upload path
        j_result = await db.execute(select(Job).where(Job.id == job_id))
        job_obj = j_result.scalar_one_or_none()
        project_id = job_obj.project_id if job_obj else job_id

        job_dir = Path(settings.UPLOAD_DIR) / project_id / job_id
        tts_dir = job_dir / "tts"
        tts_dir.mkdir(parents=True, exist_ok=True)

        for seg in segments:
            voice_kwargs = await _resolve_voice(seg, db)
            out_raw_path = tts_dir / f"seg_{seg.id}_raw.wav"
            out_final_path = tts_dir / f"seg_{seg.id}.wav"
            result_data = await tts_client.synthesize(
                text=seg.khmer_text,
                output_path=str(out_raw_path),
                **voice_kwargs,
            )

            if result_data["success"]:
                from app.services.audio_extractor import apply_audio_effects
                new_duration = await apply_audio_effects(
                    input_path=str(out_raw_path),
                    output_path=str(out_final_path),
                    volume_db=seg.volume_db,
                    voice_filter=seg.voice_filter,
                    voice_speed=seg.voice_speed,
                )
                seg.tts_audio_path    = str(out_final_path)
                seg.tts_duration_secs = new_duration
                await db.commit()
