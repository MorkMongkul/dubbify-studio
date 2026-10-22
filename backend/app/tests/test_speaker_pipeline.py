"""
Stage 2 / speaker detection behaviour at the pipeline + route level:

- re-analysis never destroys the user's existing segments unless the new ones
  are fully ready (atomic swap), and a failure leaves the editor usable;
- new voices never reuse labels already taken in the project, and the
  project's named/voiced speakers are offered to the ASR as reference voices;
- "Detect speakers" relabels existing segments without touching text/audio;
- a server restart never strands a job that already has segments.

ASR and translation are replaced at the service boundary; everything else
(routes, sessions, models) runs for real against the in-memory test DB.
"""
from pathlib import Path

import pytest
from sqlalchemy import select

import app.core.database as core_db
from app.api.routes import jobs as jobs_routes
from app.models.models import Job, JobStatus, Project, Segment, Speaker, Voice
from app.services import asr, pipeline, pipeline_common, story_notes
from app.services.diarizer import DiarizedSegment
from app.services.pipeline_common import recover_orphaned_jobs
from app.services.translator import TranslationReport


@pytest.fixture
def uploads(monkeypatch, tmp_path):
    """Point every module that builds media paths at a temp upload root; 'configure' Gemini."""
    monkeypatch.setattr(pipeline.settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(pipeline.settings, "GEMINI_API_KEY", "test-key")
    return tmp_path


@pytest.fixture
def background_db(monkeypatch, db_sessionmaker):
    """Background tasks open their own sessions — point them at the test DB."""
    monkeypatch.setattr(core_db, "AsyncSessionLocal", db_sessionmaker)
    return db_sessionmaker


def _vocals(uploads: Path, project_id: str, job_id: str) -> Path:
    d = uploads / project_id / job_id
    d.mkdir(parents=True, exist_ok=True)
    p = d / "vocals.wav"
    p.write_bytes(b"RIFFfake")
    return p


async def _seed(db_sessionmaker, *, status=JobStatus.COMPLETED, segments=(), speakers=()):
    """
    segments: tuples (start, end, source_text, khmer_text, speaker_label|None, tts_path)
    speakers: tuples (label, display_name, has_voice)
    """
    async with db_sessionmaker() as s:
        project = Project(name="Series")
        s.add(project)
        await s.flush()
        voice = None
        if any(has_voice for _, _, has_voice in speakers):
            voice = Voice(name="Narrator", mode="design")
            s.add(voice)
            await s.flush()
        by_label = {}
        for label, display, has_voice in speakers:
            sp = Speaker(project_id=project.id, label=label, display_name=display,
                         voice_id=voice.id if has_voice else None)
            s.add(sp)
            await s.flush()
            by_label[label] = sp
        job = Job(project_id=project.id, status=status, progress=100, video_path="v.mp4")
        s.add(job)
        await s.flush()
        for start, end, src, km, label, tts in segments:
            s.add(Segment(job_id=job.id, start_time=start, end_time=end, source_text=src,
                          khmer_text=km, tts_audio_path=tts,
                          speaker_id=by_label[label].id if label else None))
        await s.commit()
        return project.id, job.id


async def _job(db_sessionmaker, job_id):
    async with db_sessionmaker() as s:
        return (await s.execute(select(Job).where(Job.id == job_id))).scalar_one()


async def _segments(db_sessionmaker, job_id):
    async with db_sessionmaker() as s:
        rows = (await s.execute(
            select(Segment, Speaker.label).outerjoin(Speaker, Speaker.id == Segment.speaker_id)
            .where(Segment.job_id == job_id).order_by(Segment.start_time)
        )).all()
        return [(seg, label) for seg, label in rows]


async def _speaker_labels(db_sessionmaker, project_id):
    async with db_sessionmaker() as s:
        rows = (await s.execute(select(Speaker.label).where(Speaker.project_id == project_id))).scalars().all()
        return sorted(rows)


def _fake_transcribe(segments, capture=None):
    async def fake(audio_path, references=(), taken_labels=(), language="zh"):
        if capture is not None:
            capture.update(references=list(references), taken_labels=set(taken_labels), language=language)
        return segments, False
    return fake


async def _fake_translate(texts, source_lang, target_lang, segments_context=None, story="", cast=""):
    return [f"km:{t}" for t in texts], TranslationReport()


# ── Stage 2 (run_analysis_pipeline) ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_translation_uses_the_project_story(monkeypatch, uploads, background_db):
    project_id, job_id = await _seed(background_db, segments=[(0.0, 2.0, "你好", "", None, "")])
    stories: list[str] = []

    async def capture(texts, source_lang, target_lang, segments_context=None, story="", cast=""):
        stories.append(story)
        return [f"km:{t}" for t in texts], TranslationReport()

    monkeypatch.setattr(pipeline, "translate_batch_with_report", capture)
    await pipeline.run_translation(job_id)                   # Translate missing lines, no notes yet
    assert stories == [""]

    async with background_db() as s:
        project = await s.get(Project, project_id)
        project.story_notes = story_notes.apply_user_edit(None, {"character": {"zh": "王医生", "km": "វ៉ាង", "about": "man"}})
        await s.commit()
    _vocals(uploads, project_id, job_id)
    monkeypatch.setattr(pipeline, "transcribe_audio", _fake_transcribe([
        DiarizedSegment("SPEAKER_00", 0.0, 1.0, "unknown", "adult", "新"),
    ]))
    await pipeline.run_analysis_pipeline(job_id)             # Analyze, with notes
    assert len(stories) == 2 and "王医生 → វ៉ាង" in stories[1]


@pytest.mark.asyncio
async def test_reanalysis_failure_keeps_previous_segments(monkeypatch, uploads, background_db):
    project_id, job_id = await _seed(background_db, segments=[
        (0.0, 2.0, "旧的一", "edited by hand", None, ""),
        (3.0, 5.0, "旧的二", "also edited", None, ""),
    ])
    _vocals(uploads, project_id, job_id)
    monkeypatch.setattr(pipeline, "transcribe_audio", _fake_transcribe([
        DiarizedSegment("SPEAKER_00", 0.0, 1.0, "unknown", "adult", "新"),
    ]))

    async def broken_translate(*args, **kwargs):
        raise RuntimeError("Gemini 503")

    monkeypatch.setattr(pipeline, "translate_batch_with_report", broken_translate)
    await pipeline.run_analysis_pipeline(job_id)

    rows = await _segments(background_db, job_id)
    assert [seg.khmer_text for seg, _ in rows] == ["edited by hand", "also edited"]
    job = await _job(background_db, job_id)
    assert job.status == JobStatus.COMPLETED          # editor stays usable
    assert "kept" in job.error_msg


@pytest.mark.asyncio
async def test_analysis_swaps_in_new_segments_and_drops_unused_auto_speakers(monkeypatch, uploads, background_db):
    project_id, job_id = await _seed(
        background_db,
        speakers=[("SPEAKER_00", "SPEAKER_00", False)],
        segments=[(0.0, 2.0, "旧", "old", "SPEAKER_00", "")],
    )
    _vocals(uploads, project_id, job_id)
    monkeypatch.setattr(pipeline, "transcribe_audio", _fake_transcribe([
        DiarizedSegment("SPEAKER_01", 0.0, 1.0, "unknown", "adult", "你好"),
        DiarizedSegment("SPEAKER_02", 1.0, 2.0, "unknown", "adult", "再见"),
    ]))
    monkeypatch.setattr(pipeline, "translate_batch_with_report", _fake_translate)
    await pipeline.run_analysis_pipeline(job_id)

    rows = await _segments(background_db, job_id)
    assert [(seg.source_text, seg.khmer_text, label) for seg, label in rows] == [
        ("你好", "km:你好", "SPEAKER_01"), ("再见", "km:再见", "SPEAKER_02"),
    ]
    assert await _speaker_labels(background_db, project_id) == ["SPEAKER_01", "SPEAKER_02"]
    job = await _job(background_db, job_id)
    assert job.status == JobStatus.COMPLETED and job.error_msg == ""


@pytest.mark.asyncio
async def test_analysis_offers_named_speakers_as_references_and_reserves_taken_labels(
        monkeypatch, uploads, background_db):
    # Episode 1 (another job) has a speaker the user named "Lin Dong" + an unnamed auto speaker.
    project_id, ep1 = await _seed(
        background_db,
        speakers=[("SPEAKER_00", "Lin Dong", False), ("SPEAKER_01", "SPEAKER_01", False)],
        segments=[(10.0, 13.5, "我已经有工作了", "x", "SPEAKER_00", ""),
                  (20.0, 22.0, "随便", "y", "SPEAKER_01", "")],
    )
    _vocals(uploads, project_id, ep1)
    async with background_db() as s:
        ep2 = Job(project_id=project_id, status=JobStatus.STEMS_READY, video_path="v2.mp4")
        s.add(ep2)
        await s.commit()
        ep2_id = ep2.id
    _vocals(uploads, project_id, ep2_id)

    async def fake_slice(audio_path, start, end, out_path):
        Path(out_path).write_bytes(b"RIFFref")

    monkeypatch.setattr(asr, "_slice_chunk", fake_slice)
    seen = {}
    monkeypatch.setattr(pipeline, "transcribe_audio", _fake_transcribe(
        [DiarizedSegment("SPEAKER_00", 0.0, 1.0, "unknown", "adult", "又见面了")], capture=seen))
    monkeypatch.setattr(pipeline, "translate_batch_with_report", _fake_translate)
    await pipeline.run_analysis_pipeline(ep2_id)

    assert {"SPEAKER_00", "SPEAKER_01"} <= seen["taken_labels"]
    assert [(r.label, r.text) for r in seen["references"]] == [("SPEAKER_00", "我已经有工作了")]
    rows = await _segments(background_db, ep2_id)
    assert rows[0][1] == "SPEAKER_00"           # matched to the existing named speaker
    assert await _speaker_labels(background_db, project_id) == ["SPEAKER_00", "SPEAKER_01"]


@pytest.mark.asyncio
async def test_analysis_database_error_does_not_leave_the_job_stuck(monkeypatch, uploads, background_db):
    project_id, job_id = await _seed(background_db, status=JobStatus.STEMS_READY)
    _vocals(uploads, project_id, job_id)
    # start_time=None violates NOT NULL at flush time → the session needs a rollback.
    monkeypatch.setattr(pipeline, "transcribe_audio", _fake_transcribe([
        DiarizedSegment("SPEAKER_00", None, 1.0, "unknown", "adult", "坏"),
    ]))
    monkeypatch.setattr(pipeline, "translate_batch_with_report", _fake_translate)
    await pipeline.run_analysis_pipeline(job_id)
    job = await _job(background_db, job_id)
    assert job.status == JobStatus.FAILED
    assert job.error_msg


@pytest.mark.asyncio
async def test_analysis_warns_about_lines_that_could_not_be_translated(monkeypatch, uploads, background_db):
    project_id, job_id = await _seed(background_db, status=JobStatus.STEMS_READY)
    _vocals(uploads, project_id, job_id)
    monkeypatch.setattr(pipeline, "transcribe_audio", _fake_transcribe([
        DiarizedSegment("SPEAKER_00", 0.0, 1.0, "unknown", "adult", "你好"),
        DiarizedSegment("SPEAKER_00", 1.5, 2.5, "unknown", "adult", "再见"),
    ]))

    async def partly_failed(texts, *args, **kwargs):
        return ["សួស្តី", ""], TranslationReport(fallback=0, failed=1)

    monkeypatch.setattr(pipeline, "translate_batch_with_report", partly_failed)
    await pipeline.run_analysis_pipeline(job_id)
    job = await _job(background_db, job_id)
    assert job.status == JobStatus.COMPLETED
    assert "couldn't be translated" in job.error_msg
    assert [seg.khmer_text for seg, _ in await _segments(background_db, job_id)] == ["សួស្តី", ""]


# ── Analyze route ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_analyze_route_allows_reanalysis_of_a_completed_job(client, monkeypatch, uploads, background_db):
    project_id, job_id = await _seed(background_db, segments=[(0.0, 1.0, "a", "b", None, "")])
    _vocals(uploads, project_id, job_id)
    started = []

    async def record(job_id_arg, max_speakers=None):
        started.append(job_id_arg)

    monkeypatch.setattr(jobs_routes, "run_analysis_pipeline", record)
    resp = await client.post(f"/api/v1/jobs/{job_id}/analyze")
    assert resp.status_code == 202
    assert started == [job_id]
    assert (await _job(background_db, job_id)).status == JobStatus.DIARIZING


@pytest.mark.asyncio
async def test_analyze_route_refuses_a_job_that_is_already_running(client, uploads, background_db):
    project_id, job_id = await _seed(background_db, status=JobStatus.DIARIZING)
    _vocals(uploads, project_id, job_id)
    resp = await client.post(f"/api/v1/jobs/{job_id}/analyze")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_analyze_route_explains_when_stage_one_produced_no_audio(client, uploads, background_db):
    _, job_id = await _seed(background_db, status=JobStatus.FAILED)
    resp = await client.post(f"/api/v1/jobs/{job_id}/analyze")
    assert resp.status_code == 400
    assert "upload" in resp.json()["detail"].lower()


# ── Detect speakers ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_detect_speakers_relabels_without_touching_text_or_audio(client, monkeypatch, uploads, background_db):
    project_id, job_id = await _seed(
        background_db,
        speakers=[("SPEAKER_00", "SPEAKER_00", False)],
        segments=[(0.0, 2.0, "老板说", "km1", "SPEAKER_00", "/tts/a.wav"),
                  (3.0, 5.0, "员工说", "km2", "SPEAKER_00", "/tts/b.wav"),
                  (6.0, 8.0, "老板又说", "km3", "SPEAKER_00", "")],
    )
    _vocals(uploads, project_id, job_id)
    seen = {}
    # A fresh analysis of the audio — its own timing, not the segments' — is
    # matched to the existing segments by overlap.
    fresh = [
        DiarizedSegment("SPEAKER_01", 0.2, 2.4, "unknown", "adult", "老板说"),
        DiarizedSegment("SPEAKER_02", 2.9, 5.1, "unknown", "adult", "员工说"),
        DiarizedSegment("SPEAKER_01", 5.8, 8.0, "unknown", "adult", "老板又说"),
    ]
    monkeypatch.setattr(pipeline, "transcribe_audio", _fake_transcribe(fresh, capture=seen))
    resp = await client.post(f"/api/v1/jobs/{job_id}/detect-speakers")
    assert resp.status_code == 202

    assert "SPEAKER_00" in seen["taken_labels"]
    rows = await _segments(background_db, job_id)
    assert [label for _, label in rows] == ["SPEAKER_01", "SPEAKER_02", "SPEAKER_01"]
    assert [(seg.source_text, seg.khmer_text, seg.tts_audio_path) for seg, _ in rows] == [
        ("老板说", "km1", "/tts/a.wav"), ("员工说", "km2", "/tts/b.wav"), ("老板又说", "km3", ""),
    ]
    assert await _speaker_labels(background_db, project_id) == ["SPEAKER_01", "SPEAKER_02"]
    assert (await _job(background_db, job_id)).status == JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_detect_speakers_explains_a_missing_api_key(client, monkeypatch, uploads, background_db):
    project_id, job_id = await _seed(background_db, segments=[(0.0, 2.0, "a", "b", None, "")])
    _vocals(uploads, project_id, job_id)
    monkeypatch.setattr(pipeline.settings, "GEMINI_API_KEY", "")
    resp = await client.post(f"/api/v1/jobs/{job_id}/detect-speakers")
    assert resp.status_code == 400
    assert "GEMINI_API_KEY" in resp.json()["detail"]
    assert (await _job(background_db, job_id)).status == JobStatus.COMPLETED   # untouched


@pytest.mark.asyncio
async def test_detect_speakers_needs_existing_segments(client, uploads, background_db):
    project_id, job_id = await _seed(background_db)
    _vocals(uploads, project_id, job_id)
    resp = await client.post(f"/api/v1/jobs/{job_id}/detect-speakers")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_detect_speakers_failure_restores_the_job(client, monkeypatch, uploads, background_db):
    project_id, job_id = await _seed(background_db, segments=[(0.0, 2.0, "a", "b", None, "")])
    _vocals(uploads, project_id, job_id)

    async def broken(*args, **kwargs):
        raise RuntimeError("Gemini down")

    monkeypatch.setattr(pipeline, "transcribe_audio", broken)
    resp = await client.post(f"/api/v1/jobs/{job_id}/detect-speakers")
    assert resp.status_code == 202
    job = await _job(background_db, job_id)
    assert job.status == JobStatus.COMPLETED and "Gemini down" in job.error_msg


@pytest.mark.asyncio
async def test_detect_speakers_keeps_existing_speakers_when_nothing_was_heard(
        client, monkeypatch, uploads, background_db):
    project_id, job_id = await _seed(
        background_db,
        speakers=[("SPEAKER_00", "Lin Dong", False)],
        segments=[(0.0, 2.0, "a", "b", "SPEAKER_00", "")],
    )
    _vocals(uploads, project_id, job_id)

    async def nothing_heard(audio_path, references=(), taken_labels=(), language="zh"):
        return [DiarizedSegment("SPEAKER_03", 0.0, 2.0, "unknown", "adult", "")], True

    monkeypatch.setattr(pipeline, "transcribe_audio", nothing_heard)
    await client.post(f"/api/v1/jobs/{job_id}/detect-speakers")
    rows = await _segments(background_db, job_id)
    assert [label for _, label in rows] == ["SPEAKER_00"]
    job = await _job(background_db, job_id)
    assert job.status == JobStatus.COMPLETED and job.error_msg


# ── Restart recovery ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_restart_recovery_never_strands_a_job_that_has_segments(background_db):
    _, mixing_job = await _seed(background_db, status=JobStatus.MIXING,
                                segments=[(0.0, 1.0, "a", "b", None, "")])
    _, extracting_job = await _seed(background_db, status=JobStatus.EXTRACTING)
    await recover_orphaned_jobs()

    mixed = await _job(background_db, mixing_job)
    assert mixed.status == JobStatus.COMPLETED and "restart" in mixed.error_msg.lower()
    assert (await _job(background_db, extracting_job)).status == JobStatus.FAILED


@pytest.mark.asyncio
async def test_an_interrupted_separation_retry_leaves_the_episode_as_it_was(uploads, background_db):
    """Try again keeps the stems until it succeeds, so quitting mid-way mustn't fail the episode."""
    waiting_project, waiting = await _seed(background_db, status=JobStatus.SEPARATING)
    voiced_project, voiced = await _seed(background_db, status=JobStatus.SEPARATING,
                                         segments=[(0.0, 1.0, "你好", "សួស្តី", None, "")])
    subtitle_project, subtitle = await _seed(background_db, status=JobStatus.SEPARATING,
                                             segments=[(0.0, 1.0, "你好", "", None, "")])
    for project_id, job_id in ((waiting_project, waiting), (voiced_project, voiced), (subtitle_project, subtitle)):
        _vocals(uploads, project_id, job_id)
        (uploads / project_id / job_id / "no_vocals.wav").write_bytes(b"RIFFfake")
    await recover_orphaned_jobs()

    assert (await _job(background_db, waiting)).status == JobStatus.STEMS_READY
    assert (await _job(background_db, voiced)).status == JobStatus.COMPLETED
    # Subtitle lines that were never translated: still waiting for Analyze
    assert (await _job(background_db, subtitle)).status == JobStatus.STEMS_READY


@pytest.mark.asyncio
async def test_try_again_returns_an_episode_waiting_for_analyze_to_waiting(client, uploads, background_db, monkeypatch):
    """A subtitle episode has its lines before Analyze; Try again used to mark it completed."""
    project_id, job_id = await _seed(background_db, status=JobStatus.STEMS_READY,
                                     segments=[(0.0, 1.0, "你好", "", None, "")])
    async with background_db() as s:
        job = (await s.execute(select(Job).where(Job.id == job_id))).scalar_one()
        job.video_path = str(_vocals(uploads, project_id, job_id))   # any existing file
        await s.commit()
    seen = []

    async def retry(job_id, return_status):
        seen.append(return_status)
    monkeypatch.setattr("app.api.routes.jobs.run_separation_retry", retry)
    assert (await client.post(f"/api/v1/jobs/{job_id}/separate")).status_code == 202
    assert seen == [JobStatus.STEMS_READY]


# ── Translate missing lines (recovery after a translation outage) ─────────────

@pytest.mark.asyncio
async def test_translate_missing_fills_only_empty_lines_and_keeps_edits(client, monkeypatch, uploads, background_db):
    project_id, job_id = await _seed(
        background_db,
        speakers=[("SPEAKER_00", "Xiaoyu", False), ("SPEAKER_01", "Jun", False)],
        segments=[(0.0, 2.0, "你好", "", "SPEAKER_00", ""),
                  (3.0, 5.0, "谢谢", "edited by hand", "SPEAKER_01", "/tts/b.wav"),
                  (6.0, 8.0, "再见", "", "SPEAKER_01", ""),
                  (9.0, 9.5, "", "", None, "")],          # no transcription: nothing to translate
    )
    seen = {}

    async def fake_translate(texts, source_lang, target_lang, segments_context=None, story="", cast=""):
        seen.update(texts=list(texts), labels=[c["speaker_label"] for c in segments_context])
        return [f"km:{t}" for t in texts], TranslationReport()

    monkeypatch.setattr(pipeline, "translate_batch_with_report", fake_translate)
    resp = await client.post(f"/api/v1/jobs/{job_id}/translate")
    assert resp.status_code == 202
    assert seen == {"texts": ["你好", "再见"], "labels": ["SPEAKER_00", "SPEAKER_01"]}
    rows = await _segments(background_db, job_id)
    assert [seg.khmer_text for seg, _ in rows] == ["km:你好", "edited by hand", "km:再见", ""]
    job = await _job(background_db, job_id)
    assert job.status == JobStatus.COMPLETED and job.error_msg == ""


@pytest.mark.asyncio
async def test_translate_missing_never_overwrites_a_line_typed_while_it_ran(client, monkeypatch, uploads, background_db):
    _project_id, job_id = await _seed(background_db, segments=[(0.0, 2.0, "你好", "", None, "")])

    async def slow_translate(texts, source_lang, target_lang, segments_context=None, story="", cast=""):
        async with background_db() as s:     # the user types a translation meanwhile
            seg = (await s.execute(select(Segment).where(Segment.job_id == job_id))).scalar_one()
            seg.khmer_text = "typed by the user"
            await s.commit()
        return ["km:你好"], TranslationReport()

    monkeypatch.setattr(pipeline, "translate_batch_with_report", slow_translate)
    await client.post(f"/api/v1/jobs/{job_id}/translate")
    rows = await _segments(background_db, job_id)
    assert rows[0][0].khmer_text == "typed by the user"


@pytest.mark.asyncio
async def test_translate_missing_reports_lines_that_still_failed(client, monkeypatch, uploads, background_db):
    _project_id, job_id = await _seed(background_db, segments=[(0.0, 2.0, "你好", "", None, ""),
                                                                (3.0, 4.0, "再见", "", None, "")])

    async def half(texts, source_lang, target_lang, segments_context=None, story="", cast=""):
        return ["", "km:再见"], TranslationReport(failed=1, gemini_error="Gemini 503: overloaded")

    monkeypatch.setattr(pipeline, "translate_batch_with_report", half)
    await client.post(f"/api/v1/jobs/{job_id}/translate")
    job = await _job(background_db, job_id)
    assert job.status == JobStatus.COMPLETED
    assert "1 line(s) couldn't be translated" in job.error_msg and "overloaded" in job.error_msg


@pytest.mark.asyncio
async def test_translate_missing_with_nothing_to_do_says_so(client, uploads, background_db):
    _project_id, job_id = await _seed(background_db, segments=[(0.0, 2.0, "你好", "km", None, "")])
    resp = await client.post(f"/api/v1/jobs/{job_id}/translate")
    assert resp.status_code == 400
    assert "already" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_translate_missing_refuses_a_busy_session(client, uploads, background_db):
    _project_id, job_id = await _seed(background_db, status=JobStatus.TRANSLATING,
                                      segments=[(0.0, 2.0, "你好", "", None, "")])
    resp = await client.post(f"/api/v1/jobs/{job_id}/translate")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_translate_missing_failure_restores_the_job(client, monkeypatch, uploads, background_db):
    _project_id, job_id = await _seed(background_db, segments=[(0.0, 2.0, "你好", "", None, "")])

    async def broken(*args, **kwargs):
        raise RuntimeError("translator crashed")

    monkeypatch.setattr(pipeline, "translate_batch_with_report", broken)
    resp = await client.post(f"/api/v1/jobs/{job_id}/translate")
    assert resp.status_code == 202
    job = await _job(background_db, job_id)
    assert job.status == JobStatus.COMPLETED and "translator crashed" in job.error_msg


@pytest.mark.asyncio
async def test_a_speaker_heard_with_a_picked_voice_is_offered_as_a_reference(monkeypatch, uploads, background_db):
    """
    The app picks voices for speakers nobody voiced (auto_voice.py). Once lines
    were voiced with a pick, detecting speakers again (or the next episode) must
    recognise that speaker — else it's replaced and gets a different voice.
    A picked voice nobody has heard yet doesn't make a speaker a reference.
    """
    project_id, ep1 = await _seed(
        background_db,
        speakers=[("SPEAKER_00", "SPEAKER_00", True), ("SPEAKER_01", "SPEAKER_01", True)],
        segments=[(10.0, 13.5, "我已经有工作了", "x", "SPEAKER_00", "tts/seg_1.wav"),
                  (20.0, 23.5, "随便看看吧", "y", "SPEAKER_01", "")],
    )
    async with background_db() as s:
        for speaker in (await s.execute(select(Speaker).where(Speaker.project_id == project_id))).scalars():
            speaker.voice_auto = True
        await s.commit()
    _vocals(uploads, project_id, ep1)
    async with background_db() as s:
        ep2 = Job(project_id=project_id, status=JobStatus.STEMS_READY, video_path="v2.mp4")
        s.add(ep2)
        await s.commit()
        ep2_id = ep2.id
    _vocals(uploads, project_id, ep2_id)

    async def fake_slice(audio_path, start, end, out_path):
        Path(out_path).write_bytes(b"RIFFref")

    monkeypatch.setattr(asr, "_slice_chunk", fake_slice)
    seen = {}
    monkeypatch.setattr(pipeline, "transcribe_audio", _fake_transcribe(
        [DiarizedSegment("SPEAKER_00", 0.0, 1.0, "unknown", "adult", "又见面了")], capture=seen))
    monkeypatch.setattr(pipeline, "translate_batch_with_report", _fake_translate)
    await pipeline.run_analysis_pipeline(ep2_id)

    assert [(r.label, r.text) for r in seen["references"]] == [("SPEAKER_00", "我已经有工作了")]


# ── Who is speaking, for the translation ──────────────────────────────────────

@pytest.mark.asyncio
async def test_translation_hears_who_each_speaker_is(monkeypatch, uploads, background_db):
    """
    Gemini used to get only "SPEAKER_01" and guessed genders — so a man could
    answer ចាស. It now hears the name the user gave, the Story notes' Khmer
    name, and man/woman/age from the voice the user chose — or, for a speaker
    with no voice yet, what the pitch of their lines suggests.
    """
    project_id, job_id = await _seed(
        background_db,
        speakers=[("SPEAKER_01", "谢三", True), ("SPEAKER_02", "SPEAKER_02", False), ("SPEAKER_03", "SPEAKER_03", False)],
        segments=[(0.0, 2.0, "你好", "", "SPEAKER_01", ""),
                  (2.0, 4.0, "走吧", "", "SPEAKER_02", ""),
                  (4.0, 6.0, "嗯", "", "SPEAKER_03", "")],
    )
    _vocals(uploads, project_id, job_id)
    async with background_db() as s:
        voice = (await s.execute(select(Voice))).scalar_one()
        voice.gender, voice.age_group = "man", "20s"
        project = await s.get(Project, project_id)
        project.story_notes = story_notes.apply_user_edit(
            None, {"character": {"zh": "谢三", "km": "សៀ សាន", "about": "", "speaker": "SPEAKER_01"}})
        await s.commit()
    pitches = {2.0: 220.0}                                    # SPEAKER_02 sounds like a woman; SPEAKER_03 can't be measured
    monkeypatch.setattr(pipeline_common.auto_voice, "_pitch_of_spans",
                        lambda spans: pitches.get(spans[0][1]) if spans else None)
    casts: list[str] = []

    async def capture(texts, source_lang, target_lang, segments_context=None, story="", cast=""):
        casts.append(cast)
        return [f"km:{t}" for t in texts], TranslationReport()

    monkeypatch.setattr(pipeline, "translate_batch_with_report", capture)
    await pipeline.run_translation(job_id)

    lines = {line.split(":")[0].lstrip("- "): line for line in casts[0].splitlines() if line.startswith("- ")}
    assert "谢三" in lines["SPEAKER_01"] and "សៀ សាន" in lines["SPEAKER_01"] and "a man, 20s" in lines["SPEAKER_01"]
    assert "probably a woman" in lines["SPEAKER_02"]
    assert "not known" in lines["SPEAKER_03"]


@pytest.mark.asyncio
async def test_analysis_tells_the_translation_who_is_speaking(monkeypatch, uploads, background_db):
    project_id, job_id = await _seed(background_db, speakers=[("SPEAKER_00", "王医生", True)])
    _vocals(uploads, project_id, job_id)
    monkeypatch.setattr(pipeline, "transcribe_audio", _fake_transcribe([
        DiarizedSegment("SPEAKER_00", 0.0, 1.0, "unknown", "adult", "你好"),
        DiarizedSegment("SPEAKER_01", 1.0, 2.0, "unknown", "adult", "谢谢"),
    ]))
    monkeypatch.setattr(pipeline_common.auto_voice, "_pitch_of_spans", lambda spans: 120.0)
    casts: list[str] = []

    async def capture(texts, source_lang, target_lang, segments_context=None, story="", cast=""):
        casts.append(cast)
        return [f"km:{t}" for t in texts], TranslationReport()

    monkeypatch.setattr(pipeline, "translate_batch_with_report", capture)
    await pipeline.run_analysis_pipeline(job_id)
    assert "SPEAKER_00: 王医生" in casts[0]
    assert "SPEAKER_01: probably a man" in casts[0]          # a new voice: measured, not named
