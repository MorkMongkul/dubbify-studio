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

from app.services import timeline

STEMS = ("music", "voices")
MIN_PIECE_SECS = 0.1


def _get(p, name, default=None):
    return p.get(name, default) if isinstance(p, dict) else getattr(p, name, default)


def normalize(stem: str, pieces, duration: float) -> list[dict]:
    """The pieces to store: each inside [0, duration] and at least
    MIN_PIECE_SECS long (dropped otherwise), its volume inside the clip range,
    its fades no longer than it; a piece overlapping the one before it is cut
    at that piece's end — never moved. Music pieces are always exported."""
    from types import SimpleNamespace

    raw = []
    for p in pieces:
        fields = p.model_dump() if hasattr(p, "model_dump") else dict(p)
        start = max(0.0, float(fields.get("start_time") or 0.0))
        end = float(fields.get("end_time") or 0.0)
        if duration > 0:
            start, end = min(start, duration), min(end, duration)
        raw.append({**fields, "start_time": round(start, 3), "end_time": round(end, 3)})
    out: list[dict] = []
    for f in sorted(raw, key=lambda f: (f["start_time"], f["end_time"])):
        if out and f["start_time"] < out[-1]["end_time"]:
            f["start_time"] = out[-1]["end_time"]
        if f["end_time"] - f["start_time"] < MIN_PIECE_SECS - 1e-9:
            continue
        vol = float(f.get("volume_db") or 0.0)
        f["volume_db"] = max(timeline.VOLUME_MIN_DB, min(timeline.VOLUME_MAX_DB, vol))
        shape = SimpleNamespace(start_time=f["start_time"], end_time=f["end_time"],
                                fade_in_secs=f.get("fade_in_secs") or 0.0, fade_out_secs=f.get("fade_out_secs") or 0.0)
        timeline.clamp_fades(shape)
        f["fade_in_secs"], f["fade_out_secs"] = shape.fade_in_secs, shape.fade_out_secs
        f["in_export"] = True if stem == "music" else bool(f.get("in_export"))
        if not f.get("id"):
            f.pop("id", None)
        out.append(f)
    return out


def piece_gain_at(pieces, t: float) -> float:
    """A stem's gain at `t`: the piece's volume × its linear fades; 0 between pieces."""
    for p in pieces:
        a, b = _get(p, "start_time"), _get(p, "end_time")
        if a <= t < b:
            gain = 10 ** ((_get(p, "volume_db") or 0.0) / 20)
            fade_in, fade_out = _get(p, "fade_in_secs") or 0.0, _get(p, "fade_out_secs") or 0.0
            shape = 1.0
            if fade_in > 0:
                shape = min(shape, (t - a) / fade_in)
            if fade_out > 0:
                shape = min(shape, (b - t) / fade_out)
            return gain * max(0.0, shape)
    return 0.0


def pieces_volume_expr(pieces, duration: float, export_only: bool = False) -> str | None:
    """piece_gain_at as an ffmpeg `volume` expression over `t` (pieces never
    overlap, so a sum of gated pieces is the gain). None when there is nothing
    to do: the music as one whole untouched piece, or no original-voices piece
    switched on (export_only)."""
    chosen = [p for p in pieces if _get(p, "in_export")] if export_only else list(pieces)
    if export_only and not chosen:
        return None
    if (not export_only and len(chosen) == 1 and _get(chosen[0], "start_time") <= 0.001
            and _get(chosen[0], "end_time") >= duration - 0.001 and not _get(chosen[0], "volume_db")
            and not _get(chosen[0], "fade_in_secs") and not _get(chosen[0], "fade_out_secs")):
        return None
    if not chosen:
        return "0"
    terms = []
    for p in chosen:
        a, b = _get(p, "start_time"), _get(p, "end_time")
        gain = 10 ** ((_get(p, "volume_db") or 0.0) / 20)
        shape = "1"
        fade_in, fade_out = _get(p, "fade_in_secs") or 0.0, _get(p, "fade_out_secs") or 0.0
        if fade_in > 0:
            shape = f"min({shape},(t-{a:.3f})/{fade_in:.3f})"
        if fade_out > 0:
            shape = f"min({shape},({b:.3f}-t)/{fade_out:.3f})"
        terms.append(f"gte(t,{a:.3f})*lt(t,{b:.3f})*{gain:.6f}*{shape}")
    return "+".join(terms)


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
