"""
Single-voice dub analysis (0.5.7): transcription on this Mac (no speakers), every line translated as free natural
spoken Khmer for one voice, lines saved without speakers — and Gemini never hears the video unless Analyze is pressed.
Normal projects never come here. Gemini, the Mac transcription and line timing are replaced at their boundaries.
"""
import json

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.models import Job, JobStatus, Project, Segment
from app.services import pipeline
from app.services.diarizer import DiarizedSegment
from app.services.single_voice import pipeline as sv_pipeline, translate as sv_translate
from app.services.translator import TranslationReport

pytestmark = pytest.mark.asyncio


# ── the translation request ──────────────────────────────────────

async def test_the_request_asks_for_free_spoken_khmer_that_fits(monkeypatch):
    payloads = []

    async def post(payload, timeout):
        payloads.append(payload)
        return {"candidates": [{"content": {"parts": [{"text": "1. ទៅណា?\n2. អត់ទេ!"}]}}]}
    monkeypatch.setattr(sv_translate.translator, "_post_translation", post)
    texts, report = await sv_translate.translate(["你去哪？", "不要！"], [1.2, 0.8], "STORY BLOCK",
                                                 [("你好", "សួស្តី")], "km-KH-PisethNeural")
    assert texts == ["ទៅណា?", "អត់ទេ!"] and report.failed == report.fallback == 0
    prompt = payloads[0]["contents"][0]["parts"][0]["text"]
    assert "1. (1.2 s) 你去哪？" in prompt and "2. (0.8 s) 不要！" in prompt
    assert "STORY BLOCK" in prompt and "你好 → សួស្តី" in prompt
    assert "WHO IS SPEAKING" not in prompt and "a man's voice" in prompt


async def test_lines_gemini_leaves_out_are_asked_for_again_then_go_to_the_fallback(monkeypatch):
    answers = iter(["1. មួយ", ""])   # line 2 missing, then the retry answers with nothing for it

    async def post(payload, timeout):
        return {"candidates": [{"content": {"parts": [{"text": next(answers, "")}]}}]}
    monkeypatch.setattr(sv_translate.translator, "_post_translation", post)
    monkeypatch.setattr(sv_translate.translator, "_deep_translate_many", lambda lines, s, t: ["google"] * len(lines))
    texts, report = await sv_translate.translate(["一", "二"], [1.0, 1.0], "", [], "km-KH-SreymomNeural")
    assert texts == ["មួយ", "google"] and report.fallback == 1


# ── which earlier lines teach ────────────────────────────────────

async def test_only_checked_khmer_teaches(db_sessionmaker):
    async with db_sessionmaker() as s:
        project = Project(name="Quick", dub_mode="single_voice")
        s.add(project)
        await s.flush()
        old = Job(project_id=project.id, status=JobStatus.COMPLETED, video_path="a.mp4")
        done = Job(project_id=project.id, status=JobStatus.COMPLETED, video_path="b.mp4")
        new = Job(project_id=project.id, status=JobStatus.STEMS_READY, video_path="c.mp4")
        s.add_all([old, done, new])
        await s.flush()
        from datetime import datetime
        done.finished_at = datetime.now()
        s.add_all([
            Segment(job_id=old.id, start_time=0, end_time=1, source_text="甲", khmer_text="draft", khmer_machine="draft",
                    tts_audio_path="/v/1.wav"),                                         # voiced, never read: no
            Segment(job_id=old.id, start_time=1, end_time=2, source_text="乙", khmer_text="fixed", khmer_machine="x"),
            Segment(job_id=old.id, start_time=2, end_time=3, source_text="丙", khmer_text="ok", khmer_machine="ok",
                    is_approved=True),
            Segment(job_id=done.id, start_time=0, end_time=1, source_text="丁", khmer_text="d", khmer_machine="d"),
        ])
        await s.commit()
        pairs = await sv_translate.memory(s, project.id, new.id)
    assert sorted(pairs) == sorted([("乙", "fixed"), ("丙", "ok"), ("丁", "d")])


# ── one episode ──────────────────────────────────────────────────

async def _episode(db_sessionmaker, monkeypatch, tmp_path, *, mac_ready=True, old_lines=False):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr("app.core.database.AsyncSessionLocal", db_sessionmaker)
    async with db_sessionmaker() as s:
        project = Project(name="Quick", dub_mode="single_voice", voice_name="km-KH-PisethNeural", voice_rate=0,
                          auto_export=False)
        s.add(project)
        await s.flush()
        job = Job(project_id=project.id, status=JobStatus.DIARIZING, video_path=str(tmp_path / "v.mp4"),
                  duration_secs=10.0)
        s.add(job)
        await s.flush()
        if old_lines:
            s.add(Segment(job_id=job.id, start_time=0, end_time=1, source_text="旧", khmer_text="old"))
        await s.commit()
        ids = (project.id, job.id)
    folder = tmp_path / ids[0] / ids[1]
    folder.mkdir(parents=True)
    (folder / "vocals.wav").write_bytes(b"RIFF")
    monkeypatch.setattr(sv_pipeline.local_asr, "use_mac", lambda language: mac_ready)
    heard = []

    async def mac(vocals, audio, names, taken_labels):
        heard.append("mac")
        return [DiarizedSegment("SPEAKER_00", 0.5, 2.0, "man", "adult", "你好"),
                DiarizedSegment("SPEAKER_01", 3.0, 4.5, "woman", "adult", "走吧")]
    monkeypatch.setattr(sv_pipeline.local_asr, "transcribe", mac)

    async def gemini(*args, **kwargs):
        heard.append("gemini")
        return [DiarizedSegment("SPEAKER_00", 0.5, 2.0, "man", "adult", "你好")], False
    monkeypatch.setattr(sv_pipeline, "transcribe_audio", gemini)

    async def timing(vocals, lines, language):
        return 0
    monkeypatch.setattr(sv_pipeline.line_timing, "refine", timing)
    return ids, heard


def _translator(monkeypatch, error=None):
    async def translate(texts, durations, story, memory, voice):
        if error:
            raise error
        return [f"km:{t}" for t in texts], TranslationReport()
    monkeypatch.setattr(sv_pipeline.sv_translate, "translate", translate)


async def test_an_episode_is_transcribed_here_and_translated_without_speakers(db_sessionmaker, monkeypatch, tmp_path):
    (project_id, job_id), heard = await _episode(db_sessionmaker, monkeypatch, tmp_path)
    _translator(monkeypatch)
    await sv_pipeline.run_analysis(job_id)
    async with db_sessionmaker() as s:
        job = (await s.execute(select(Job).where(Job.id == job_id))).scalar_one()
        lines = (await s.execute(select(Segment).where(Segment.job_id == job_id).order_by(Segment.start_time))).scalars().all()
    assert heard == ["mac"] and job.status == JobStatus.COMPLETED
    assert [(l.source_text, l.khmer_text, l.khmer_machine) for l in lines] == [
        ("你好", "km:你好", "km:你好"), ("走吧", "km:走吧", "km:走吧")]
    assert all(l.speaker_id is None and l.who_check is None for l in lines)


async def test_gemini_never_hears_the_video_without_a_click(db_sessionmaker, monkeypatch, tmp_path):
    (project_id, job_id), heard = await _episode(db_sessionmaker, monkeypatch, tmp_path, mac_ready=False)
    _translator(monkeypatch)
    await sv_pipeline.run_analysis(job_id)
    async with db_sessionmaker() as s:
        job = (await s.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert heard == [] and job.status == JobStatus.STEMS_READY
    assert "Press Analyze to transcribe with Gemini" in job.error_msg


async def test_analyze_lets_gemini_transcribe(db_sessionmaker, monkeypatch, tmp_path):
    (project_id, job_id), heard = await _episode(db_sessionmaker, monkeypatch, tmp_path, mac_ready=False)
    _translator(monkeypatch)
    await sv_pipeline.run_analysis(job_id, allow_gemini=True)
    assert heard == ["gemini"]


async def test_a_failed_translation_keeps_the_old_lines(db_sessionmaker, monkeypatch, tmp_path):
    (project_id, job_id), heard = await _episode(db_sessionmaker, monkeypatch, tmp_path, old_lines=True)
    _translator(monkeypatch, error=RuntimeError("Gemini 402"))
    await sv_pipeline.run_analysis(job_id)
    async with db_sessionmaker() as s:
        job = (await s.execute(select(Job).where(Job.id == job_id))).scalar_one()
        lines = (await s.execute(select(Segment).where(Segment.job_id == job_id))).scalars().all()
    assert job.status == JobStatus.COMPLETED and "402" in job.error_msg
    assert [l.khmer_text for l in lines] == ["old"]


async def test_a_long_video_gets_a_warning(db_sessionmaker, monkeypatch, tmp_path):
    (project_id, job_id), heard = await _episode(db_sessionmaker, monkeypatch, tmp_path)
    async with db_sessionmaker() as s:
        job = (await s.execute(select(Job).where(Job.id == job_id))).scalar_one()
        job.duration_secs = sv_pipeline.MAX_SECS + 60
        await s.commit()
    _translator(monkeypatch)
    await sv_pipeline.run_analysis(job_id)
    async with db_sessionmaker() as s:
        job = (await s.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert job.status == JobStatus.COMPLETED and "40 minutes" in job.error_msg


async def test_fully_automatic_goes_on_to_the_voices(db_sessionmaker, monkeypatch, tmp_path):
    (project_id, job_id), heard = await _episode(db_sessionmaker, monkeypatch, tmp_path)
    async with db_sessionmaker() as s:
        project = (await s.execute(select(Project).where(Project.id == project_id))).scalar_one()
        project.auto_export = True
        await s.commit()
    _translator(monkeypatch)
    went = []

    async def voices(job_id, export_path):
        went.append((job_id, export_path))
    monkeypatch.setattr(sv_pipeline, "voice_and_export", voices)
    await sv_pipeline.run_analysis(job_id)
    assert went == [(job_id, None)]


# ── the hooks in existing code ───────────────────────────────────

async def _uploaded(db_sessionmaker, monkeypatch, tmp_path, dub_mode):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "AFTER_UPLOAD", "wait")
    monkeypatch.setattr("app.core.database.AsyncSessionLocal", db_sessionmaker)

    async def duration(path):
        return 60.0

    async def rate(path):
        return 25.0

    async def extract(path, output_dir):
        return str(tmp_path / "source_audio.wav")

    async def separate(audio, out, duration):
        return str(tmp_path / "vocals.wav"), str(tmp_path / "no_vocals.wav"), False
    monkeypatch.setattr(pipeline, "get_video_duration", duration)
    monkeypatch.setattr(pipeline, "video_frame_rate", rate)
    monkeypatch.setattr(pipeline, "extract_audio", extract)
    monkeypatch.setattr(pipeline, "separate_vocals_bgm", separate)
    ran = []

    async def single(job_id, allow_gemini=False):
        ran.append(("single", allow_gemini))

    async def normal(job_id, max_speakers=None):
        ran.append(("normal", None))
    monkeypatch.setattr(sv_pipeline, "run_analysis", single)
    monkeypatch.setattr(pipeline, "run_analysis_pipeline", normal)
    async with db_sessionmaker() as s:
        project = Project(name="P", dub_mode=dub_mode)
        s.add(project)
        await s.flush()
        job = Job(project_id=project.id, video_path="v.mp4", status=JobStatus.PENDING)
        s.add(job)
        await s.commit()
        return job.id, ran


async def test_an_upload_to_a_single_voice_project_goes_on_by_itself(db_sessionmaker, monkeypatch, tmp_path):
    job_id, ran = await _uploaded(db_sessionmaker, monkeypatch, tmp_path, "single_voice")
    await pipeline.run_pipeline(job_id)
    assert ran == [("single", False)]


async def test_an_upload_to_a_normal_project_still_waits(db_sessionmaker, monkeypatch, tmp_path):
    job_id, ran = await _uploaded(db_sessionmaker, monkeypatch, tmp_path, "characters")
    await pipeline.run_pipeline(job_id)
    assert ran == []


async def test_analyze_on_a_single_voice_project_runs_its_pipeline(client, db_sessionmaker, monkeypatch, tmp_path):
    ran = []

    async def single(job_id, allow_gemini=False):
        ran.append(allow_gemini)
    monkeypatch.setattr("app.api.routes.jobs.sv_pipeline.run_analysis", single)
    monkeypatch.setattr("app.api.routes.jobs._require_audio", lambda job: None)
    async with db_sessionmaker() as s:
        project = Project(name="P", dub_mode="single_voice")
        s.add(project)
        await s.flush()
        job = Job(project_id=project.id, video_path="v.mp4", status=JobStatus.STEMS_READY)
        s.add(job)
        await s.commit()
        job_id = job.id
    resp = await client.post(f"/api/v1/jobs/{job_id}/analyze")
    assert resp.status_code == 202 and ran == [True]
