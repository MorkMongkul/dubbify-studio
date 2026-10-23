"""Shared helpers for the timeline tests: a job, its lines, real voice files."""
import numpy as np
import soundfile as sf

from app.models.models import Job, JobStatus, Segment


async def _make_job(client, db_sessionmaker) -> str:
    resp = await client.post("/api/v1/projects/", json={"name": "Ep"})
    project_id = resp.json()["id"]
    async with db_sessionmaker() as session:
        job = Job(project_id=project_id, status=JobStatus.COMPLETED, progress=100)
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


async def _add_segment(db_sessionmaker, job_id: str, start: float, end: float,
                       lane: int = 0, tts_path: str = "", **fields) -> str:
    """A line; with `tts_path` it has a voice (1 s long unless tts_duration_secs is given)."""
    fields = {"khmer_text": "ខ្មែរ", "tts_duration_secs": 1.0 if tts_path else None, **fields}
    async with db_sessionmaker() as session:
        seg = Segment(job_id=job_id, start_time=start, end_time=end, lane_index=lane,
                      tts_audio_path=tts_path, **fields)
        session.add(seg)
        await session.commit()
        await session.refresh(seg)
        return seg.id


def _write_clip(dir_path, seg_id: str, duration: float) -> str:
    """Write matching final + raw wavs; returns the final path."""
    final = dir_path / f"seg_{seg_id}.wav"
    raw = dir_path / f"seg_{seg_id}_raw.wav"
    samples = np.zeros(int(duration * 22050), dtype=np.float32)
    sf.write(str(raw), samples, 22050)
    sf.write(str(final), samples, 22050)
    return str(final)
