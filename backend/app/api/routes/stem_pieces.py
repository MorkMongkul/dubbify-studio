"""
app/api/routes/stem_pieces.py
The music & effects and the original voices in pieces (0.3.2) — see
app/services/stem_pieces.py for the rules.
"""
from typing import List, Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.models import Job, StemPiece
from app.schemas.schemas import StemPieceLists, StemPieceResponse, StemPiecesUpdate
from app.services import stem_pieces

router = APIRouter(tags=["Stem pieces"])

Stem = Literal["music", "voices"]


async def _job_or_404(db: AsyncSession, job_id: str) -> Job:
    job = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/jobs/{job_id}/stem-pieces", response_model=StemPieceLists)
async def list_stem_pieces(job_id: str, db: AsyncSession = Depends(get_db, scope="function")):
    """Both stems' pieces, in time order (the first time: one whole piece each)."""
    job = await _job_or_404(db, job_id)
    pieces = await stem_pieces.pieces_of(db, job)
    lists = {stem: [p for p in pieces if p.stem == stem] for stem in stem_pieces.STEMS}
    return StemPieceLists(**lists, made=bool(job.stem_pieces_made))


@router.put("/jobs/{job_id}/stem-pieces/{stem}", response_model=List[StemPieceResponse])
async def set_stem_pieces(
    job_id: str,
    stem: Stem,
    payload: StemPiecesUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Replace a stem's pieces with the list the editor sends (an edit, an undo)."""
    job = await _job_or_404(db, job_id)
    cleaned = stem_pieces.normalize(stem, payload.pieces, job.duration_secs or 0.0)
    await db.execute(delete(StemPiece).where(StemPiece.job_id == job_id, StemPiece.stem == stem))
    rows = [StemPiece(job_id=job_id, stem=stem, **p) for p in cleaned]
    db.add_all(rows)
    await db.commit()
    return sorted(rows, key=lambda p: p.start_time)
