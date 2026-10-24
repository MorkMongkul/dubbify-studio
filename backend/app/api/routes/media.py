"""
app/api/routes/media.py
The Media tab (0.4.0): a project's media library, each episode's media clips
(edited as one list, like the stem pieces) and media templates, which are only
ever applied by hand — a new upload is dubbed first.
"""
import asyncio
import json
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.database as core_db
from app.api.routes.jobs import _save_upload
from app.core.config import settings
from app.core.database import get_db
from app.models.models import Job, MediaAsset, MediaClip, MediaTemplate, Overlay, Project
from app.schemas.schemas import (
    MediaAssetResponse, MediaClipList, MediaClipResponse, MediaClipsUpdate, MediaDeleted, MediaTemplateApplied,
    MediaTemplateCreate, MediaTemplateRename, MediaTemplateResponse,
)
from app.services import media
from app.services.media_assets import IMAGE_EXTS, VIDEO_EXTS, prepare_asset, probe_asset

router = APIRouter(tags=["Media"])

CLIP_FIELDS = ("asset_id", "lane_index", "start_time", "end_time", "source_in", "x", "y", "width", "height", "volume_db")
BOX = ("x", "y", "width", "height")


async def _prepare(asset_id: str) -> None:
    # Read at call time: tests swap the session factory
    await prepare_asset(asset_id, core_db.AsyncSessionLocal)


async def _get_or_404(db: AsyncSession, model, key: str, what: str):
    row = await db.get(model, key)
    if row is None:
        raise HTTPException(status_code=404, detail=f"{what} not found")
    return row


# ── The library ───────────────────────────────────────────────

@router.post("/projects/{project_id}/media", response_model=MediaAssetResponse, status_code=status.HTTP_201_CREATED)
async def upload_media(project_id: str, background_tasks: BackgroundTasks, file: UploadFile = File(...),
                       db: AsyncSession = Depends(get_db, scope="function")):
    """Add an image or a video to the project's library; its files are made in the background."""
    await _get_or_404(db, Project, project_id, "Project")
    name = Path(file.filename or "media").name
    ext = Path(name).suffix.lower()
    if ext not in IMAGE_EXTS | VIDEO_EXTS:
        raise HTTPException(status_code=415, detail="Images (PNG, JPG, WebP) and videos (MP4, MOV, M4V, MKV, WebM, GIF) only")
    asset_id = str(uuid.uuid4())
    folder = Path(settings.UPLOAD_DIR) / project_id / "media" / asset_id
    dest = folder / f"original{ext}"
    await _save_upload(file, dest)
    try:
        facts = await probe_asset(dest)
    except ValueError:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(status_code=415, detail="This file isn't an image or a video Movelo can read")
    if facts.animated_webp:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(status_code=415, detail="Animated WebP isn't supported — save it as GIF or ProRes 4444 .mov")
    asset = MediaAsset(id=asset_id, project_id=project_id, name=name[:255], kind=facts.kind, path=str(dest),
                       width=facts.width, height=facts.height, duration_secs=facts.duration_secs,
                       has_alpha=facts.has_alpha, alpha_kept=facts.alpha_kept, has_audio=facts.has_audio,
                       status="preparing")
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    background_tasks.add_task(_prepare, asset_id)
    return asset


@router.get("/projects/{project_id}/media", response_model=list[MediaAssetResponse])
async def list_media(project_id: str, db: AsyncSession = Depends(get_db, scope="function")):
    rows = await db.execute(select(MediaAsset).where(MediaAsset.project_id == project_id)
                            .order_by(MediaAsset.created_at.desc()))
    return rows.scalars().all()


@router.delete("/media/{asset_id}", response_model=MediaDeleted)
async def delete_media(asset_id: str, db: AsyncSession = Depends(get_db, scope="function")):
    """Remove media from the library, with its clips in every episode and its items in templates."""
    asset = await _get_or_404(db, MediaAsset, asset_id, "Media")
    clips = (await db.execute(select(MediaClip).where(MediaClip.asset_id == asset_id))).scalars().all()
    episodes = len({c.job_id for c in clips})
    await db.execute(delete(MediaClip).where(MediaClip.asset_id == asset_id))
    templates = (await db.execute(select(MediaTemplate).where(MediaTemplate.project_id == asset.project_id))).scalars().all()
    for t in templates:
        items = json.loads(t.items or "[]")
        kept = [i for i in items if i.get("asset_id") != asset_id]
        if len(kept) != len(items):
            t.items = json.dumps(kept)
    folder = Path(asset.path).parent
    await db.delete(asset)
    await db.commit()
    if folder.name == asset_id and folder.exists():
        await asyncio.to_thread(shutil.rmtree, folder, ignore_errors=True)
    return MediaDeleted(removed_clips=len(clips), episodes=episodes)


# ── An episode's media clips ──────────────────────────────────

async def _clip_list(db: AsyncSession, job: Job) -> MediaClipList:
    clips = (await db.execute(select(MediaClip).where(MediaClip.job_id == job.id)
                              .order_by(MediaClip.lane_index, MediaClip.start_time))).scalars().all()
    flags = media.prune_flags(media.flags_from(job.media_track_flags), clips)
    old = (await db.execute(select(func.count()).select_from(Overlay).where(Overlay.job_id == job.id))).scalar_one()
    return MediaClipList(clips=[MediaClipResponse.model_validate(c) for c in clips],
                         track_flags={str(k): v for k, v in flags.items()}, old_overlays=old > 0)


@router.get("/jobs/{job_id}/media-clips", response_model=MediaClipList)
async def get_media_clips(job_id: str, db: AsyncSession = Depends(get_db, scope="function")):
    return await _clip_list(db, await _get_or_404(db, Job, job_id, "Job"))


async def _store_clips(db: AsyncSession, job: Job, wanted: list[dict]) -> None:
    """Make the job's clips exactly `wanted` (already normalized); a clip moved or resized
    teaches its media where it was last placed."""
    existing = {c.id: c for c in (await db.execute(select(MediaClip).where(MediaClip.job_id == job.id))).scalars().all()}
    assets = {a.id: a for a in (await db.execute(select(MediaAsset).where(
        MediaAsset.id.in_({c["asset_id"] for c in wanted})))).scalars().all()}
    keep = set()
    for c in wanted:
        row = existing.get(c.get("id") or "")
        box = {k: c[k] for k in BOX}
        if row is None:
            row = MediaClip(id=c.get("id") or str(uuid.uuid4()), job_id=job.id)
            db.add(row)
            moved = False
        else:
            moved = any(abs(getattr(row, k) - box[k]) > 1e-9 for k in BOX)
        for k in CLIP_FIELDS:
            setattr(row, k, c[k])
        keep.add(row.id)
        if moved:
            assets[c["asset_id"]].last_box = json.dumps(box)
    for cid, row in existing.items():
        if cid not in keep:
            await db.delete(row)


@router.put("/jobs/{job_id}/media-clips", response_model=MediaClipList)
async def set_media_clips(job_id: str, body: MediaClipsUpdate, db: AsyncSession = Depends(get_db, scope="function")):
    """Replace the episode's media clips (one undo step in the editor is one such request)."""
    job = await _get_or_404(db, Job, job_id, "Job")
    assets = {a.id: a for a in (await db.execute(select(MediaAsset).where(
        MediaAsset.project_id == job.project_id))).scalars().all()}
    try:
        wanted = media.normalize([c.model_dump() for c in body.clips], assets, job.duration_secs or 0.0)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    await _store_clips(db, job, wanted)
    flags = (media.flags_from({k: v.model_dump() for k, v in body.track_flags.items()})
             if body.track_flags is not None else media.flags_from(job.media_track_flags))
    job.media_track_flags = media.flags_to(media.prune_flags(flags, wanted))
    await db.commit()
    return await _clip_list(db, job)


# ── Templates ─────────────────────────────────────────────────

@router.post("/projects/{project_id}/media-templates", response_model=MediaTemplateResponse,
             status_code=status.HTTP_201_CREATED)
async def save_media_template(project_id: str, body: MediaTemplateCreate,
                              db: AsyncSession = Depends(get_db, scope="function")):
    """The episode's media clips, saved under a name."""
    job = await _get_or_404(db, Job, body.job_id, "Job")
    if job.project_id != project_id:
        raise HTTPException(status_code=400, detail="That episode isn't in this project")
    clips = (await db.execute(select(MediaClip).where(MediaClip.job_id == job.id))).scalars().all()
    items = [{k: getattr(c, k) for k in CLIP_FIELDS} for c in clips]
    template = MediaTemplate(project_id=project_id, name=body.name.strip(), items=json.dumps(items))
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return template


@router.get("/projects/{project_id}/media-templates", response_model=list[MediaTemplateResponse])
async def list_media_templates(project_id: str, db: AsyncSession = Depends(get_db, scope="function")):
    rows = await db.execute(select(MediaTemplate).where(MediaTemplate.project_id == project_id)
                            .order_by(MediaTemplate.created_at.desc()))
    return rows.scalars().all()


@router.patch("/media-templates/{template_id}", response_model=MediaTemplateResponse)
async def rename_media_template(template_id: str, body: MediaTemplateRename,
                                db: AsyncSession = Depends(get_db, scope="function")):
    template = await _get_or_404(db, MediaTemplate, template_id, "Template")
    template.name = body.name.strip()
    await db.commit()
    await db.refresh(template)
    return template


@router.delete("/media-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_media_template(template_id: str, db: AsyncSession = Depends(get_db, scope="function")):
    await db.delete(await _get_or_404(db, MediaTemplate, template_id, "Template"))
    await db.commit()


@router.post("/media-templates/{template_id}/apply/{job_id}", response_model=MediaTemplateApplied)
async def apply_media_template(template_id: str, job_id: str, db: AsyncSession = Depends(get_db, scope="function")):
    """Add a template's clips to an episode, beside its own (never over them, never moving them)."""
    template = await _get_or_404(db, MediaTemplate, template_id, "Template")
    job = await _get_or_404(db, Job, job_id, "Job")
    if job.project_id != template.project_id:
        raise HTTPException(status_code=400, detail="That episode isn't in this template's project")
    assets = {a.id: a for a in (await db.execute(select(MediaAsset).where(
        MediaAsset.project_id == job.project_id))).scalars().all()}
    items = json.loads(template.items or "[]")
    usable = [i for i in items if i.get("asset_id") in assets]
    existing = [{"id": c.id, **{k: getattr(c, k) for k in CLIP_FIELDS}}
                for c in (await db.execute(select(MediaClip).where(MediaClip.job_id == job.id))).scalars().all()]
    episode = job.duration_secs or max((i["end_time"] for i in usable), default=0.0)
    made = media.fit_template(usable, episode, existing)
    try:
        wanted = media.normalize(existing + made, assets, job.duration_secs or 0.0)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    await _store_clips(db, job, wanted)
    await db.commit()
    listed = await _clip_list(db, job)
    return MediaTemplateApplied(clips=listed.clips, skipped=len(items) - len(usable))
