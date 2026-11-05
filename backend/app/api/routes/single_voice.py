"""Single-voice dub's own route (0.5.7): Generate voices & export."""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.models import Job, JobStatus, Project, Segment
from app.services import single_voice
from app.services.pipeline_common import RUNNING_STATUSES
from app.services.single_voice import voices

router = APIRouter(prefix="/single-voice", tags=["Single-voice dub"])


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    export_path: str | None = None


@router.post("/jobs/{job_id}/export", status_code=202)
async def voice_and_export(job_id: str, payload: ExportRequest, background_tasks: BackgroundTasks,
                           db: AsyncSession = Depends(get_db, scope="function")):
    """Voice every line that has no current voice with the project's Edge voice, then export (when all have one)."""
    from app.api.routes.tts import _validated_export_path

    job = (await db.execute(select(Job).where(Job.id == job_id).with_for_update())).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    project = (await db.execute(select(Project).where(Project.id == job.project_id))).scalar_one_or_none()
    if not single_voice.is_single_voice(project):
        raise HTTPException(status_code=400, detail="This isn't a single-voice dub project")
    if job.status in RUNNING_STATUSES:
        raise HTTPException(status_code=409, detail="This episode is busy — wait for it to finish.")
    has_lines = (await db.execute(select(Segment.id).where(Segment.job_id == job_id).limit(1))).scalar_one_or_none()
    if has_lines is None:
        raise HTTPException(status_code=400, detail="This episode has no lines yet.")
    export_path = (payload.export_path or "").strip()
    export_path = _validated_export_path(export_path) if export_path else None
    job.status, job.progress, job.error_msg = JobStatus.SYNTHESIZING, 5, ""   # claimed: a second click gets 409
    await db.commit()
    background_tasks.add_task(voices.voice_and_export, job_id, export_path)
    return {"job_id": job_id, "status": JobStatus.SYNTHESIZING.value}
