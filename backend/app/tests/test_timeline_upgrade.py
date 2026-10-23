"""
Upgrading to the 0.3.0 timeline: existing episodes must sound the same
(a voice the old app sped up to fit gets that speed as its visible setting),
lines with no voice take their text length, and the conversion runs once.
New analyses give each line its actor's span and its text's length.
"""
import pytest
from sqlalchemy import select

import app.core.database as core_db
from app.api.routes import jobs as jobs_routes
from app.models.models import Job, JobStatus, Project, Segment
from app.services import pipeline, timeline
from app.services.diarizer import DiarizedSegment
from app.services.timeline import estimate_secs, overlaps
from app.services.translator import TranslationReport


@pytest.fixture
def background_db(monkeypatch, db_sessionmaker):
    monkeypatch.setattr(core_db, "AsyncSessionLocal", db_sessionmaker)
    return db_sessionmaker


async def _old_job(db_sessionmaker, segments):
    """segments: dicts of Segment fields; the job predates 0.3.0 (timeline_version NULL)."""
    async with db_sessionmaker() as s:
        project = Project(name="Series")
        s.add(project)
        await s.flush()
        job = Job(project_id=project.id, status=JobStatus.COMPLETED, progress=100)
        s.add(job)
        await s.flush()
        job.timeline_version = None   # NULL, as add_missing_columns leaves it
        ids = []
        for fields in segments:
            fields = {"khmer_text": "ខ្មែរ", "lane_index": 0, **fields}
            seg = Segment(job_id=job.id, **fields)
            s.add(seg)
            await s.flush()
            # Rows from before 0.3.0 read NULL in the new columns
            seg.voice_offset = None
            seg.fit_rate = None
            ids.append(seg.id)
        await s.commit()
        return job.id, ids


async def _get(db_sessionmaker, seg_id):
    async with db_sessionmaker() as s:
        return await s.get(Segment, seg_id)


def _voiced(start, end, tts, **kw):
    return {"start_time": start, "end_time": end, "tts_audio_path": "/x/seg.wav", "tts_duration_secs": tts, **kw}


@pytest.mark.asyncio
async def test_upgrade_keeps_a_sped_up_voice_sounding_the_same(background_db):
    _, (sid,) = await _old_job(background_db, [_voiced(1.0, 3.0, 2.8)])
    assert await timeline.convert_old_jobs() == 1
    s = await _get(background_db, sid)
    assert (s.fit_rate, s.end_time, s.voice_offset, s.voice_text) == (pytest.approx(1.4), pytest.approx(3.0), 0.0, s.khmer_text)
    assert (s.source_start, s.source_end) == (1.0, 3.0)


@pytest.mark.asyncio
async def test_upgrade_caps_the_speed_at_2x_and_lets_the_clip_grow(background_db):
    _, (sid,) = await _old_job(background_db, [_voiced(0.0, 2.0, 6.0)])
    await timeline.convert_old_jobs()
    s = await _get(background_db, sid)
    assert (s.fit_rate, s.end_time) == (pytest.approx(2.0), pytest.approx(3.0))


@pytest.mark.asyncio
async def test_upgrade_ends_a_short_voice_where_it_ends(background_db):
    _, (sid,) = await _old_job(background_db, [_voiced(0.0, 2.0, 1.2)])
    await timeline.convert_old_jobs()
    s = await _get(background_db, sid)
    assert (s.fit_rate, s.end_time) == (pytest.approx(1.0), pytest.approx(1.2))


@pytest.mark.asyncio
async def test_upgrade_keeps_a_manual_speed(background_db):
    _, (sid,) = await _old_job(background_db, [_voiced(0.0, 2.0, 2.5, voice_speed=1.3)])
    await timeline.convert_old_jobs()
    s = await _get(background_db, sid)
    assert (s.fit_rate, s.end_time) == (pytest.approx(1.0), pytest.approx(2.5))


@pytest.mark.asyncio
async def test_upgrade_sizes_voiceless_lines_by_their_text(background_db):
    _, (sid,) = await _old_job(background_db, [{"start_time": 4.0, "end_time": 4.5, "khmer_text": "ក" * 30}])
    await timeline.convert_old_jobs()
    s = await _get(background_db, sid)
    assert s.end_time == pytest.approx(5.95)
    assert (s.source_start, s.source_end) == (4.0, 4.5)


@pytest.mark.asyncio
async def test_upgrade_moves_clips_that_now_overlap_to_another_track(background_db):
    _, (a, b) = await _old_job(background_db, [
        _voiced(0.0, 1.0, 2.0, voice_speed=1.3),   # manual speed: grows to 2.0
        _voiced(1.5, 2.5, 1.0),
    ])
    await timeline.convert_old_jobs()
    sa, sb = await _get(background_db, a), await _get(background_db, b)
    assert (sa.start_time, sa.end_time, sb.start_time, sb.end_time) == (0.0, pytest.approx(2.0), 1.5, pytest.approx(2.5))
    assert sa.lane_index != sb.lane_index


@pytest.mark.asyncio
async def test_upgrade_runs_once(background_db):
    job_id, (sid,) = await _old_job(background_db, [_voiced(1.0, 3.0, 2.8)])
    assert await timeline.convert_old_jobs() == 1
    first = await _get(background_db, sid)
    assert await timeline.convert_old_jobs() == 0
    again = await _get(background_db, sid)
    assert (again.end_time, again.fit_rate, again.voice_offset) == (first.end_time, first.fit_rate, first.voice_offset)
    async with background_db() as s:
        assert (await s.get(Job, job_id)).timeline_version == 1


@pytest.mark.asyncio
async def test_new_jobs_are_not_converted(client, background_db, monkeypatch, tmp_path):
    monkeypatch.setattr(jobs_routes.settings, "UPLOAD_DIR", str(tmp_path))

    async def no_subtitles(**kwargs):
        return None

    async def no_pipeline(job_id):
        return None

    monkeypatch.setattr(jobs_routes, "extract_subtitle", no_subtitles)
    monkeypatch.setattr(jobs_routes, "run_pipeline", no_pipeline)
    project_id = (await client.post("/api/v1/projects/", json={"name": "Ep"})).json()["id"]
    resp = await client.post(f"/api/v1/jobs/upload/{project_id}", files={"file": ("ep.mp4", b"fake", "video/mp4")})
    assert resp.status_code == 202
    async with background_db() as s:
        assert (await s.get(Job, resp.json()["job_id"])).timeline_version == 1
    assert await timeline.convert_old_jobs() == 0


@pytest.mark.asyncio
async def test_analysis_gives_lines_their_detected_span_and_text_length(monkeypatch, tmp_path, background_db):
    monkeypatch.setattr(pipeline.settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(pipeline.settings, "GEMINI_API_KEY", "test-key")
    async with background_db() as s:
        project = Project(name="Series")
        s.add(project)
        await s.flush()
        job = Job(project_id=project.id, status=JobStatus.STEMS_READY, progress=30, video_path="v.mp4")
        s.add(job)
        await s.commit()
        project_id, job_id = project.id, job.id
    d = tmp_path / project_id / job_id
    d.mkdir(parents=True)
    (d / "vocals.wav").write_bytes(b"RIFFfake")

    detected = [
        DiarizedSegment("SPEAKER_00", 0.0, 0.5, "unknown", "adult", "一"),
        DiarizedSegment("SPEAKER_00", 0.6, 3.0, "unknown", "adult", "二"),
        DiarizedSegment("SPEAKER_01", 1.0, 1.4, "unknown", "adult", "三"),
    ]
    khmer = {"一": "ក" * 40, "二": "ខ" * 5, "三": "គ" * 10}

    async def fake_transcribe(audio_path, references=(), taken_labels=(), language="zh", clip_audio=None):
        return detected, False

    async def fake_translate(texts, source_lang, target_lang, segments_context=None, story="", cast="", memory=None):
        return [khmer[t] for t in texts], TranslationReport()

    monkeypatch.setattr(pipeline, "transcribe_audio", fake_transcribe)
    monkeypatch.setattr(pipeline, "translate_batch_with_report", fake_translate)
    await pipeline.run_analysis_pipeline(job_id)

    async with background_db() as s:
        rows = (await s.execute(select(Segment).where(Segment.job_id == job_id).order_by(Segment.start_time))).scalars().all()
    assert len(rows) == 3
    for row, d_seg in zip(rows, detected):
        assert (row.source_start, row.source_end) == (d_seg.start_time, d_seg.end_time)
        assert row.start_time == d_seg.start_time
        assert row.end_time == pytest.approx(row.start_time + estimate_secs(row.khmer_text))
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if (a.lane_index or 0) == (b.lane_index or 0):
                assert not overlaps(a, b)


@pytest.mark.asyncio
async def test_subtitle_lines_keep_their_cue_as_the_actor_span(monkeypatch, tmp_path, background_db):
    from app.services import subtitle_pipeline
    async with background_db() as s:
        project = Project(name="Series")
        s.add(project)
        await s.flush()
        job = Job(project_id=project.id, status=JobStatus.STEMS_READY, progress=30, video_path="v.mp4")
        s.add(job)
        await s.flush()
        s.add(Segment(job_id=job.id, start_time=1.0, end_time=2.0, source_start=1.0, source_end=2.0,
                      source_text="你好", khmer_text=""))
        await s.commit()
        project_id, job_id = project.id, job.id
    monkeypatch.setattr(subtitle_pipeline.settings, "UPLOAD_DIR", str(tmp_path))
    d = tmp_path / project_id / job_id
    d.mkdir(parents=True)
    (d / "vocals.wav").write_bytes(b"RIFFfake")

    async def fake_label(vocals_path, spans, **kwargs):
        return ["SPEAKER_00"] * len(spans), [""] * len(spans), False

    async def fake_translate(texts, source_lang, target_lang, segments_context=None, story="", cast="", memory=None):
        return ["ក" * 40 for _ in texts], TranslationReport()

    monkeypatch.setattr(subtitle_pipeline, "label_speech", fake_label)
    monkeypatch.setattr(subtitle_pipeline, "translate_batch_with_report", fake_translate)
    await subtitle_pipeline.run_subtitle_analysis_pipeline(job_id)

    async with background_db() as s:
        row = (await s.execute(select(Segment).where(Segment.job_id == job_id))).scalar_one()
    assert (row.source_start, row.source_end) == (1.0, 2.0)
    assert row.end_time == pytest.approx(1.0 + estimate_secs("ក" * 40))
