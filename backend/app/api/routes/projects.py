"""
app/api/routes/projects.py
CRUD endpoints for Projects.
"""
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from typing import List

from app.core.database import get_db
from app.core.config import settings
from app.models.models import Project, Job, Segment, Speaker
from app.schemas.schemas import ProjectCreate, ProjectResponse

router = APIRouter(prefix="/projects", tags=["Projects"])


@router.post("/", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(payload: ProjectCreate, db: AsyncSession = Depends(get_db)):
    """Create a new dubbing project."""
    project = Project(**payload.model_dump())
    db.add(project)
    await db.flush()
    await db.refresh(project)
    return project


@router.get("/", response_model=List[ProjectResponse])
async def list_projects(db: AsyncSession = Depends(get_db)):
    """List all projects."""
    result = await db.execute(select(Project).order_by(Project.created_at.desc()))
    return result.scalars().all()


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, db: AsyncSession = Depends(get_db)):
    """Get a single project by ID."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a project and all its associated data (jobs, segments, speakers, files)."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Delete children explicitly in FK-safe order (segments → jobs → speakers)
    # rather than relying on async ORM cascade.
    job_ids = (await db.execute(select(Job.id).where(Job.project_id == project_id))).scalars().all()
    if job_ids:
        await db.execute(delete(Segment).where(Segment.job_id.in_(job_ids)))
        await db.execute(delete(Job).where(Job.project_id == project_id))
    await db.execute(delete(Speaker).where(Speaker.project_id == project_id))
    await db.delete(project)
    await db.commit()

    # Remove the project's files from disk
    project_dir = Path(settings.UPLOAD_DIR) / project_id
    if project_dir.exists():
        shutil.rmtree(project_dir, ignore_errors=True)
