"""
app/api/routes/projects.py
CRUD endpoints for Projects.
"""
import asyncio
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from typing import List

from app.core.database import get_db
from app.core.config import settings
from app.models.models import Project, Job, Segment, Speaker, Overlay, ChatMessage, ChatProposedEdit, ChatThread, StemPiece
from app.schemas.schemas import ProjectCreate, ProjectUpdate, ProjectResponse
from app.services import story_notes

router = APIRouter(prefix="/projects", tags=["Projects"])

_LOGO_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


@router.post("/", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(payload: ProjectCreate, db: AsyncSession = Depends(get_db, scope="function")):
    """Create a new dubbing project."""
    data = payload.model_dump()
    intro = data.pop("story_intro").strip()   # "About this drama" starts the story notes
    project = Project(**data, story_notes=story_notes.with_intro(None, intro) if intro else None)
    db.add(project)
    await db.flush()
    await db.refresh(project)
    return project


@router.get("/", response_model=List[ProjectResponse])
async def list_projects(db: AsyncSession = Depends(get_db, scope="function")):
    """List all projects."""
    result = await db.execute(select(Project).order_by(Project.created_at.desc()))
    return result.scalars().all()


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, db: AsyncSession = Depends(get_db, scope="function")):
    """Get a single project by ID."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    payload: ProjectUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Update a project's name/description — inline rename in the editor header."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)

    await db.flush()
    await db.refresh(project)
    return project


@router.post("/{project_id}/logo", response_model=ProjectResponse)
async def upload_project_logo(
    project_id: str,
    logo: UploadFile = File(...),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Upload or replace the project's watermark logo — reused on every export."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    suffix = Path(logo.filename or "logo.png").suffix.lower()
    if suffix not in _LOGO_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported image type '{suffix}'. Allowed: {sorted(_LOGO_EXTS)}",
        )
    dest_dir = Path(settings.UPLOAD_DIR) / project_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"logo{suffix}"
    with open(dest, "wb") as out:
        shutil.copyfileobj(logo.file, out)

    project.logo_path = str(dest)
    await db.flush()
    await db.refresh(project)
    return project


@router.delete("/{project_id}/logo", response_model=ProjectResponse)
async def delete_project_logo(project_id: str, db: AsyncSession = Depends(get_db, scope="function")):
    """Remove the project's watermark logo."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.logo_path:
        old = Path(project.logo_path)
        if old.exists():
            old.unlink()
        project.logo_path = ""
        await db.flush()
        await db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: str, db: AsyncSession = Depends(get_db, scope="function")):
    """Delete a project and all its associated data (jobs, segments, speakers, files)."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Delete children explicitly in FK-safe order (chat edits/messages →
    # segments + overlays → jobs → speakers) rather than relying on async ORM
    # cascade. Overlays also point at jobs.id, so skipping them left the bulk
    # job delete violating that FK on Postgres (and silently orphaning rows on
    # SQLite, which doesn't enforce it). Chat edits reference segments AND
    # chat messages, so they must go before both.
    await db.execute(delete(ChatProposedEdit).where(ChatProposedEdit.project_id == project_id))
    await db.execute(delete(ChatMessage).where(ChatMessage.project_id == project_id))
    await db.execute(delete(ChatThread).where(ChatThread.project_id == project_id))
    job_ids = (await db.execute(select(Job.id).where(Job.project_id == project_id))).scalars().all()
    if job_ids:
        await db.execute(delete(Segment).where(Segment.job_id.in_(job_ids)))
        await db.execute(delete(Overlay).where(Overlay.job_id.in_(job_ids)))
        await db.execute(delete(StemPiece).where(StemPiece.job_id.in_(job_ids)))
        await db.execute(delete(Job).where(Job.project_id == project_id))
    await db.execute(delete(Speaker).where(Speaker.project_id == project_id))
    await db.delete(project)
    await db.commit()

    # Remove the project's files from disk (can be gigabytes — off the event loop)
    project_dir = Path(settings.UPLOAD_DIR) / project_id
    if project_dir.exists():
        await asyncio.to_thread(shutil.rmtree, project_dir, ignore_errors=True)
