"""
app/api/routes/overlays.py
Draggable/resizable video overlays — dropped images (logos, stickers) and
the burned-in subtitle box. Position/size are fractions of the video's own
dimensions, shared unchanged between the live editor canvas and export.
"""
import shutil
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update

from app.core.database import get_db
from app.core.config import settings
from app.models.models import Overlay, OverlayTemplate, Job, Project, generate_uuid
from app.schemas.schemas import (
    OverlayResponse, OverlayUpdate, OverlaySubtitleCreate, OverlayShapeCreate,
    OverlayTemplateCreate, OverlayTemplateUpdate, OverlayTemplateResponse,
)

router = APIRouter(tags=["Overlays"])

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


async def _get_job(job_id: str, db: AsyncSession) -> Job:
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/jobs/{job_id}/overlays", response_model=List[OverlayResponse])
async def list_overlays(job_id: str, db: AsyncSession = Depends(get_db)):
    """List all overlay layers for a job, in stacking (z-index) order."""
    result = await db.execute(
        select(Overlay).where(Overlay.job_id == job_id).order_by(Overlay.z_index)
    )
    return result.scalars().all()


@router.post("/jobs/{job_id}/overlays/image", response_model=OverlayResponse, status_code=status.HTTP_201_CREATED)
async def create_image_overlay(
    job_id: str,
    media: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Drop a new image (logo, sticker, etc.) onto the video canvas."""
    job = await _get_job(job_id, db)

    suffix = Path(media.filename or "overlay.png").suffix.lower()
    if suffix not in _IMAGE_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported image type '{suffix}'. Allowed: {sorted(_IMAGE_EXTS)}",
        )

    max_z = (await db.execute(
        select(func.max(Overlay.z_index)).where(Overlay.job_id == job_id)
    )).scalar() or 0

    overlay = Overlay(id=generate_uuid(), job_id=job_id, type="image", z_index=max_z + 1)
    dest_dir = Path(settings.UPLOAD_DIR) / job.project_id / job_id / "overlays"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{overlay.id}{suffix}"
    with open(dest, "wb") as out:
        shutil.copyfileobj(media.file, out)
    overlay.media_path = str(dest)

    db.add(overlay)
    await db.flush()
    await db.refresh(overlay)
    await db.commit()
    return overlay


@router.post("/jobs/{job_id}/overlays/from-project-logo", response_model=OverlayResponse, status_code=status.HTTP_201_CREATED)
async def create_overlay_from_project_logo(job_id: str, db: AsyncSession = Depends(get_db)):
    """Quick-add the project's saved watermark logo as a new overlay on this job."""
    job = await _get_job(job_id, db)
    p_result = await db.execute(select(Project).where(Project.id == job.project_id))
    project = p_result.scalar_one_or_none()
    from app.core.paths import resolve_existing
    logo_src = resolve_existing(project.logo_path) if project else None
    if not logo_src:
        raise HTTPException(status_code=400, detail="No saved project logo to add")

    max_z = (await db.execute(
        select(func.max(Overlay.z_index)).where(Overlay.job_id == job_id)
    )).scalar() or 0

    overlay = Overlay(id=generate_uuid(), job_id=job_id, type="image", z_index=max_z + 1)
    # Copy (not reference) the project logo — deleting this overlay later
    # must not remove the shared project-level logo file.
    dest_dir = Path(settings.UPLOAD_DIR) / job.project_id / job_id / "overlays"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{overlay.id}{logo_src.suffix}"
    shutil.copyfile(logo_src, dest)
    overlay.media_path = str(dest)

    db.add(overlay)
    await db.flush()
    await db.refresh(overlay)
    await db.commit()
    return overlay


@router.post("/jobs/{job_id}/overlays/subtitle", response_model=OverlayResponse, status_code=status.HTTP_201_CREATED)
async def create_subtitle_overlay(
    job_id: str,
    payload: OverlaySubtitleCreate,
    db: AsyncSession = Depends(get_db),
):
    """
    Create the job's subtitle box (only one supported — if it already exists,
    that existing row is returned unchanged rather than creating a duplicate).
    """
    await _get_job(job_id, db)

    existing = (await db.execute(
        select(Overlay).where(Overlay.job_id == job_id, Overlay.type == "subtitle")
    )).scalar_one_or_none()
    if existing:
        return existing

    max_z = (await db.execute(
        select(func.max(Overlay.z_index)).where(Overlay.job_id == job_id)
    )).scalar() or 0

    overlay = Overlay(job_id=job_id, type="subtitle", z_index=max_z + 1, **payload.model_dump())
    db.add(overlay)
    await db.flush()
    await db.refresh(overlay)
    await db.commit()
    return overlay


@router.post("/jobs/{job_id}/overlays/shape", response_model=OverlayResponse, status_code=status.HTTP_201_CREATED)
async def create_shape_overlay(
    job_id: str,
    payload: OverlayShapeCreate,
    db: AsyncSession = Depends(get_db),
):
    """
    Add a plain box (solid color or a blurred region) — typically used to
    cover something already burned into the source video, like an original-
    language subtitle, so a new subtitle overlay reads cleanly on top of it.
    Multiple are allowed, unlike the singleton subtitle box.
    """
    await _get_job(job_id, db)

    max_z = (await db.execute(
        select(func.max(Overlay.z_index)).where(Overlay.job_id == job_id)
    )).scalar() or 0

    overlay = Overlay(job_id=job_id, type="shape", z_index=max_z + 1, **payload.model_dump())
    db.add(overlay)
    await db.flush()
    await db.refresh(overlay)
    await db.commit()
    return overlay


# ── Overlay templates ("brand kit") ─────────────────────────────
# A workspace-global snapshot of one episode's full overlay layout, applied
# to other episodes in one click (or automatically on upload when default).
# Positions are fractions of video dimensions, so the same template lands
# pixel-identically on every vertical episode.

_TEMPLATE_ITEM_FIELDS = (
    "type", "media_path", "blur", "x", "y", "width", "height", "opacity",
    "z_index", "start_time", "end_time", "font_size", "color",
    "outline_color", "background_color",
)


def _template_media_dir(template_id: str) -> Path:
    return Path(settings.UPLOAD_DIR) / "templates" / template_id


async def apply_overlay_template(template: OverlayTemplate, job: Job, db: AsyncSession) -> list[Overlay]:
    """
    Stamp a template's overlays onto a job. Adds on top of whatever the job
    already has (never deletes user work); skips the subtitle box if the job
    already has one (it's a singleton per job). Image files are copied into
    the job's own overlays dir so template and job stay independently
    deletable. Caller commits.
    """
    from app.core.paths import resolve_existing

    existing = (await db.execute(
        select(Overlay).where(Overlay.job_id == job.id)
    )).scalars().all()
    max_z = max((o.z_index for o in existing), default=0)
    has_subtitle = any(o.type == "subtitle" for o in existing)

    created: list[Overlay] = []
    items = sorted(template.items or [], key=lambda it: it.get("z_index", 0))
    for item in items:
        if item.get("type") == "subtitle" and has_subtitle:
            continue

        overlay = Overlay(id=generate_uuid(), job_id=job.id)
        for field in _TEMPLATE_ITEM_FIELDS:
            if field in item and field not in ("media_path", "z_index"):
                setattr(overlay, field, item[field])
        max_z += 1
        overlay.z_index = max_z

        if item.get("type") == "image":
            src = resolve_existing(item.get("media_path", ""))
            if not src:
                continue  # template image file lost on disk — skip this item
            dest_dir = Path(settings.UPLOAD_DIR) / job.project_id / job.id / "overlays"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{overlay.id}{src.suffix}"
            shutil.copyfile(src, dest)
            overlay.media_path = str(dest)

        db.add(overlay)
        created.append(overlay)
    return created


async def apply_default_overlay_template(job: Job, db: AsyncSession) -> None:
    """Auto-stamp the default template (if any) onto a freshly created job.
    Never lets a template problem break an upload — failures only log."""
    try:
        template = (await db.execute(
            select(OverlayTemplate).where(OverlayTemplate.is_default == True)  # noqa: E712
        )).scalars().first()
        if not template:
            return
        await apply_overlay_template(template, job, db)
        await db.commit()
    except Exception as e:
        await db.rollback()
        import logging
        logging.getLogger(__name__).warning(
            f"Default overlay template not applied to job {job.id[:8]}: {e}"
        )


@router.get("/overlay-templates", response_model=List[OverlayTemplateResponse])
async def list_overlay_templates(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(OverlayTemplate).order_by(OverlayTemplate.created_at)
    )
    return result.scalars().all()


@router.post("/overlay-templates", response_model=OverlayTemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_overlay_template(
    payload: OverlayTemplateCreate,
    db: AsyncSession = Depends(get_db),
):
    """Snapshot ALL of a job's overlays into a reusable global template."""
    await _get_job(payload.from_job_id, db)
    overlays = (await db.execute(
        select(Overlay).where(Overlay.job_id == payload.from_job_id).order_by(Overlay.z_index)
    )).scalars().all()
    if not overlays:
        raise HTTPException(status_code=400, detail="This episode has no overlays to save")

    template = OverlayTemplate(id=generate_uuid(), name=payload.name, is_default=payload.set_default)
    media_dir = _template_media_dir(template.id)

    items = []
    for i, ov in enumerate(overlays):
        item = {f: getattr(ov, f) for f in _TEMPLATE_ITEM_FIELDS}
        if ov.type == "image" and ov.media_path:
            # Copy (not reference) so deleting the source episode later can't
            # hollow out the template.
            src = Path(ov.media_path)
            if not src.exists():
                continue
            media_dir.mkdir(parents=True, exist_ok=True)
            dest = media_dir / f"item{i}{src.suffix}"
            shutil.copyfile(src, dest)
            item["media_path"] = str(dest)
        items.append(item)
    if not items:
        raise HTTPException(status_code=400, detail="No overlay files found on disk to save")
    template.items = items

    if payload.set_default:
        await db.execute(update(OverlayTemplate).values(is_default=False))
    db.add(template)
    await db.flush()
    await db.refresh(template)
    await db.commit()
    return template


@router.patch("/overlay-templates/{template_id}", response_model=OverlayTemplateResponse)
async def update_overlay_template(
    template_id: str,
    payload: OverlayTemplateUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Rename a template or toggle it as the auto-applied default."""
    template = (await db.execute(
        select(OverlayTemplate).where(OverlayTemplate.id == template_id)
    )).scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    if payload.name is not None:
        template.name = payload.name
    if payload.is_default is not None:
        if payload.is_default:
            await db.execute(update(OverlayTemplate).values(is_default=False))
        template.is_default = payload.is_default

    await db.flush()
    await db.refresh(template)
    await db.commit()
    return template


@router.delete("/overlay-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_overlay_template(template_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a template and its copied media files. Overlays already
    stamped onto episodes are untouched (they own their own file copies)."""
    template = (await db.execute(
        select(OverlayTemplate).where(OverlayTemplate.id == template_id)
    )).scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    media_dir = _template_media_dir(template_id)
    if media_dir.exists():
        shutil.rmtree(media_dir, ignore_errors=True)

    await db.delete(template)
    await db.commit()


@router.post("/overlay-templates/{template_id}/apply/{job_id}", response_model=List[OverlayResponse])
async def apply_overlay_template_to_job(
    template_id: str,
    job_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Stamp the template's overlays onto an episode (adds, never replaces)."""
    template = (await db.execute(
        select(OverlayTemplate).where(OverlayTemplate.id == template_id)
    )).scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    job = await _get_job(job_id, db)

    created = await apply_overlay_template(template, job, db)
    await db.flush()
    for ov in created:
        await db.refresh(ov)
    await db.commit()
    return created


@router.patch("/overlays/{overlay_id}", response_model=OverlayResponse)
async def update_overlay(overlay_id: str, payload: OverlayUpdate, db: AsyncSession = Depends(get_db)):
    """Move, resize, restyle, or retime an overlay layer."""
    result = await db.execute(select(Overlay).where(Overlay.id == overlay_id))
    overlay = result.scalar_one_or_none()
    if not overlay:
        raise HTTPException(status_code=404, detail="Overlay not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(overlay, field, value)

    await db.flush()
    await db.refresh(overlay)
    await db.commit()
    return overlay


@router.delete("/overlays/{overlay_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_overlay(overlay_id: str, db: AsyncSession = Depends(get_db)):
    """Remove an overlay layer (and its image file, if any)."""
    result = await db.execute(select(Overlay).where(Overlay.id == overlay_id))
    overlay = result.scalar_one_or_none()
    if not overlay:
        raise HTTPException(status_code=404, detail="Overlay not found")

    if overlay.media_path:
        p = Path(overlay.media_path)
        if p.exists():
            p.unlink()

    await db.delete(overlay)
    await db.commit()
