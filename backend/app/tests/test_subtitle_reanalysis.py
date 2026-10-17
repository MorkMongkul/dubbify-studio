"""
Re-analyzing a subtitle episode translates every line again. A line whose
translation changed used to keep its old voice clip (spoken for the old
words); it now goes back to Pending. Lines that come out the same keep theirs,
and so do lines nothing could translate this time.
"""
import pytest
from sqlalchemy import select

import app.core.database as core_db
from app.core.config import settings
from app.models.models import Job, JobStatus, Project, Segment
from app.services import subtitle_pipeline


class _Report:
    def __init__(self, failed: int = 0):
        self.failed = failed

    def warning(self) -> str:
        return f"{self.failed} line(s) couldn't be translated and were left empty." if self.failed else ""


async def _subtitle_episode(db_sessionmaker, monkeypatch, tmp_path):
    """A subtitle episode with two translated, voiced lines."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(core_db, "AsyncSessionLocal", db_sessionmaker)
    async with db_sessionmaker() as db:
        project = Project(name="Ep", source_lang="zh", target_lang="km")
        db.add(project)
        await db.flush()
        job = Job(project_id=project.id, status=JobStatus.COMPLETED, subtitle_path="subs.srt")
        db.add(job)
        await db.flush()
        for i, km in enumerate(["ចាស់ ក", "ចាស់ ខ"]):
            db.add(Segment(job_id=job.id, start_time=i * 2.0, end_time=i * 2.0 + 1.5, source_text=f"第{i}句",
                           khmer_text=km, tts_audio_path=f"/voices/{i}.wav", tts_duration_secs=1.0))
        await db.commit()
        job_id, project_id = job.id, project.id
    job_dir = tmp_path / project_id / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "vocals.wav").write_bytes(b"RIFF")

    async def labels(*args, **kwargs):
        return ["SPEAKER_00", "SPEAKER_00"], [], False

    monkeypatch.setattr(subtitle_pipeline, "label_speech", labels)
    return job_id


async def _reanalyze(db_sessionmaker, monkeypatch, job_id, translations, report):
    async def translate(texts, *args, **kwargs):
        return translations, report

    monkeypatch.setattr(subtitle_pipeline, "translate_batch_with_report", translate)
    await subtitle_pipeline.run_subtitle_analysis_pipeline(job_id)
    async with db_sessionmaker() as db:
        job = await db.get(Job, job_id)
        lines = (await db.execute(select(Segment).where(Segment.job_id == job_id).order_by(Segment.start_time))).scalars().all()
        return job, [(s.khmer_text, s.tts_audio_path) for s in lines]


@pytest.mark.asyncio
async def test_lines_with_a_new_translation_lose_their_old_voice(db_sessionmaker, monkeypatch, tmp_path):
    job_id = await _subtitle_episode(db_sessionmaker, monkeypatch, tmp_path)
    # The first line's translation changed
    _, lines = await _reanalyze(db_sessionmaker, monkeypatch, job_id, ["ថ្មី ក", "ចាស់ ខ"], _Report())
    assert lines == [("ថ្មី ក", ""), ("ចាស់ ខ", "/voices/1.wav")]


@pytest.mark.asyncio
async def test_a_line_that_could_not_be_translated_again_keeps_what_it_had(db_sessionmaker, monkeypatch, tmp_path):
    job_id = await _subtitle_episode(db_sessionmaker, monkeypatch, tmp_path)
    job, lines = await _reanalyze(db_sessionmaker, monkeypatch, job_id, ["", "ចាស់ ខ"], _Report(failed=1))
    assert lines == [("ចាស់ ក", "/voices/0.wav"), ("ចាស់ ខ", "/voices/1.wav")]
    assert "kept their previous translation" in job.error_msg
    assert "left empty" not in job.error_msg
