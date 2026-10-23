"""
app/services/stem_pieces.py
The music & effects (stem "music") and the original voices (stem "voices") in
pieces (0.3.2). A piece is the part of its stem heard from start_time to
end_time — at the same time as in the video: pieces are locked to the picture
and never move — with its own volume and linear fades. Between pieces there is
silence. Pieces of one stem never overlap.

An episode starts with one whole piece per stem, so an episode nobody cut
sounds exactly as before: the music whole at its own level, the original
voices whole but out of the export (a voices piece is exported only when
switched on, `in_export`).

The editor applies the same rules (frontend/src/lib/stemPieces.ts).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

STEMS = ("music", "voices")
MIN_PIECE_SECS = 0.1


def normalize(stem: str, pieces, duration: float) -> list[dict]:
    """The pieces to store, as plain field dicts (Task 2 adds the clamping)."""
    out = []
    for p in pieces:
        fields = p.model_dump() if hasattr(p, "model_dump") else dict(p)
        if not fields.get("id"):
            fields.pop("id", None)
        if stem == "music":
            fields["in_export"] = True
        out.append(fields)
    return sorted(out, key=lambda f: f["start_time"])


async def pieces_of(db: AsyncSession, job) -> list:
    """A job's pieces; a stem with none gets its one whole piece (once the
    video's length is known)."""
    from app.models.models import StemPiece

    rows = (await db.execute(
        select(StemPiece).where(StemPiece.job_id == job.id).order_by(StemPiece.start_time)
    )).scalars().all()
    missing = [stem for stem in STEMS if not any(p.stem == stem for p in rows)]
    if missing and (job.duration_secs or 0) > 0:
        for stem in missing:
            db.add(StemPiece(job_id=job.id, stem=stem, start_time=0.0, end_time=float(job.duration_secs),
                             volume_db=0.0, fade_in_secs=0.0, fade_out_secs=0.0, in_export=stem == "music"))
        await db.commit()
        rows = (await db.execute(
            select(StemPiece).where(StemPiece.job_id == job.id).order_by(StemPiece.start_time)
        )).scalars().all()
    return list(rows)
