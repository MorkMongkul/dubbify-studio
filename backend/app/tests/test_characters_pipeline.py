"""
Characters (0.5.0) in Stage 2: who speaks each line is recognised from the
project's characters before translating, so the WHO IS SPEAKING cast and the
Khmer politeness follow the recognised characters.

Gemini, translation and the prints are replaced at the service boundary
(pipeline.transcribe_audio, pipeline.translate_batch_with_report,
characters.prints.prints_for_spans); the profiles come from real stored prints
(unit vectors: character k has voice k and face k).
"""
import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import select

import app.core.database as core_db
from app.models.models import Gender, Job, JobStatus, LinePrint, Project, Segment, Speaker
from app.services import pipeline, subtitle_pipeline
from app.services.characters import models, store
from app.services.characters import prints as prints_module
from app.services.characters.types import LinePrints
from app.services.diarizer import DiarizedSegment
from app.services.translator import TranslationReport


def voice(k: int) -> np.ndarray:
    v = np.zeros(192, np.float32)
    v[k] = 1.0
    return v


def face(k: int) -> np.ndarray:
    v = np.zeros(128, np.float32)
    v[k] = 1.0
    return v


@pytest.fixture
def uploads(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline.settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(pipeline.settings, "GEMINI_API_KEY", "test-key")
    return tmp_path


@pytest.fixture
def background_db(monkeypatch, db_sessionmaker):
    monkeypatch.setattr(core_db, "AsyncSessionLocal", db_sessionmaker)
    return db_sessionmaker


async def _project(db_sessionmaker, characters=(("SPEAKER_00", "Lin Dong"), ("SPEAKER_01", "Mei"))) -> tuple:
    """A project whose first episode has one confirmed line per character (the first voiced, the others picked by
    the user), each with its stored prints. Returns (project id, {label: speaker id})."""
    async with db_sessionmaker() as s:
        project = Project(name="Series", source_lang="zh", target_lang="km")
        s.add(project)
        await s.flush()
        episode = Job(project_id=project.id, status=JobStatus.COMPLETED, progress=100, video_path="ep1.mp4")
        s.add(episode)
        await s.flush()
        ids = {}
        for k, (label, name) in enumerate(characters):
            speaker = Speaker(project_id=project.id, label=label, display_name=name)
            s.add(speaker)
            await s.flush()
            ids[label] = speaker.id
            line = Segment(job_id=episode.id, speaker_id=speaker.id, start_time=k * 3.0, end_time=k * 3.0 + 2.0,
                           source_text=f"第{k}句", khmer_text=f"km{k}",
                           tts_audio_path="tts/seg.wav" if k == 0 else "", who_confirmed=k > 0)
            s.add(line)
            await s.flush()
            s.add(LinePrint(segment_id=line.id, job_id=episode.id, voice_print=voice(k).tobytes(),
                            face_print=face(k).tobytes(), speaking_score=0.9, model_version=store.MODEL_VERSION))
        await s.commit()
        return project.id, ids


async def _episode(db_sessionmaker, project_id: str, status=JobStatus.STEMS_READY, **fields) -> str:
    async with db_sessionmaker() as s:
        job = Job(project_id=project_id, status=status, progress=30, video_path="v.mp4", **fields)
        s.add(job)
        await s.commit()
        return job.id


async def _first_episode(db_sessionmaker) -> tuple:
    async with db_sessionmaker() as s:
        project = Project(name="New series", source_lang="zh", target_lang="km")
        s.add(project)
        await s.commit()
        project_id = project.id
    return project_id, await _episode(db_sessionmaker, project_id)


def _vocals(uploads: Path, project_id: str, job_id: str) -> Path:
    folder = uploads / project_id / job_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "vocals.wav").write_bytes(b"RIFFfake")
    return folder / "vocals.wav"


def _transcribe(monkeypatch, lines):
    """lines: (Gemini's label, start, end, text[, the voice Gemini heard])."""
    async def fake(audio_path, references=(), taken_labels=(), language="zh", clip_audio=None):
        heard = [DiarizedSegment(line[0], line[1], line[2], line[4] if len(line) > 4 else "unknown", "adult", line[3])
                 for line in lines]
        return heard, False
    monkeypatch.setattr(pipeline, "transcribe_audio", fake)


def _prints(monkeypatch, result, calls=None):
    def fake(video_path, vocals_path, spans, with_faces=True):
        if calls is not None:
            calls.append(list(spans))
        if isinstance(result, Exception):
            raise result
        return list(result)
    monkeypatch.setattr(prints_module, "prints_for_spans", fake)


def _translate(monkeypatch, module, seen=None, texts=None, meanwhile=None):
    async def fake(source, source_lang, target_lang, segments_context=None, story="", cast="", memory=None):
        if seen is not None:
            seen.update(labels=[c["speaker_label"] for c in segments_context], cast=cast)
        if meanwhile is not None:
            await meanwhile()
        out = list(texts) if texts is not None else [f"km:{t}" for t in source]
        return out, TranslationReport(failed=sum(1 for t in out if not t))
    monkeypatch.setattr(module, "translate_batch_with_report", fake)


async def _lines(db_sessionmaker, job_id: str) -> list:
    async with db_sessionmaker() as s:
        return (await s.execute(
            select(Segment, Speaker.label).outerjoin(Speaker, Speaker.id == Segment.speaker_id)
            .where(Segment.job_id == job_id).order_by(Segment.start_time)
        )).all()


async def _line_prints(db_sessionmaker, job_id: str) -> dict:
    async with db_sessionmaker() as s:
        rows = (await s.execute(select(LinePrint).where(LinePrint.job_id == job_id))).scalars().all()
        return {row.segment_id: row for row in rows}


def _store_warnings(caplog) -> list[logging.LogRecord]:
    """What recognition logged at WARNING or above."""
    return [r for r in caplog.records if r.name == store.__name__ and r.levelno >= logging.WARNING]


async def _old_confirmed_line(db_sessionmaker, project_id: str, job_id: str) -> str:
    """This episode already has a line the user confirmed on "Uncle Wang" (SPEAKER_02), 2.0–4.0 s. Returns his id."""
    async with db_sessionmaker() as s:
        uncle = Speaker(project_id=project_id, label="SPEAKER_02", display_name="Uncle Wang")
        s.add(uncle)
        await s.flush()
        s.add(Segment(job_id=job_id, speaker_id=uncle.id, start_time=2.0, end_time=4.0, source_start=2.0,
                      source_end=4.0, source_text="旧", khmer_text="km", who_confirmed=True))
        await s.commit()
        return uncle.id


async def _job(db_sessionmaker, job_id: str) -> Job:
    async with db_sessionmaker() as s:
        return await s.get(Job, job_id)


# ── Profiles ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_only_confirmed_lines_with_todays_prints_make_profiles(db_sessionmaker):
    async with db_sessionmaker() as s:
        project = Project(name="P")
        s.add(project)
        await s.flush()
        open_ep = Job(project_id=project.id, status=JobStatus.COMPLETED)
        finished = Job(project_id=project.id, status=JobStatus.COMPLETED, finished_at=datetime.now())
        this_ep = Job(project_id=project.id, status=JobStatus.COMPLETED)
        s.add_all([open_ep, finished, this_ep])
        await s.flush()
        lines = {   # one character per line: (episode, the line's fields)
            "voiced": (open_ep, {"tts_audio_path": "tts/a.wav"}),
            "approved": (open_ep, {"is_approved": True}),
            "picked": (open_ep, {"who_confirmed": True}),
            "finished": (finished, {}),
            "still_to_check": (finished, {"tts_audio_path": "tts/b.wav", "who_check": "[]"}),
            "draft": (open_ep, {}),
            "older_models": (open_ep, {"tts_audio_path": "tts/c.wav"}),
            "no_print": (open_ep, {"who_confirmed": True}),
            "this_episode": (this_ep, {"who_confirmed": True}),
        }
        ids = {}
        for k, (name, (job, fields)) in enumerate(lines.items()):
            speaker = Speaker(project_id=project.id, label=name)
            s.add(speaker)
            await s.flush()
            ids[name] = speaker.id
            line = Segment(job_id=job.id, speaker_id=speaker.id, start_time=k, end_time=k + 1.0, **fields)
            s.add(line)
            await s.flush()
            s.add(LinePrint(segment_id=line.id, job_id=job.id,
                            voice_print=None if name == "no_print" else voice(k).tobytes(),
                            model_version="older" if name == "older_models" else store.MODEL_VERSION))
        await s.commit()
        project_id, this_id = project.id, this_ep.id
    async with db_sessionmaker() as s:
        profiles = await store.project_profiles(s, project_id, exclude_job_id=this_id)
        with_this_episode = await store.project_profiles(s, project_id)
    assert set(profiles) == {ids[name] for name in ("voiced", "approved", "picked", "finished")}
    assert np.array_equal(profiles[ids["approved"]].voice, voice(1)) and profiles[ids["approved"]].face is None
    assert set(with_this_episode) == set(profiles) | {ids["this_episode"]}


# ── Carry-over on a re-analysis ───────────────────────────────────────────────

@pytest.mark.parametrize("new, old, kept", [
    ((0.0, 10.0), [((0.0, 4.9), "A")], None),                        # 49% of the new line
    ((0.0, 10.0), [((0.0, 5.1), "A")], "A"),                         # 51%
    ((0.0, 10.0), [((0.0, 5.0), "A")], None),                        # exactly half isn't more than half
    ((2.0, 3.0), [((0.0, 10.0), "A")], "A"),                         # a short new line inside a long old one
    ((0.0, 10.0), [((4.0, 5.0), "A")], None),                        # a long new line over a short old one
    ((0.0, 10.0), [((0.0, 3.0), "A"), ((5.0, 8.0), "A")], "A"),      # 30% + 30% of one character
    ((0.0, 10.0), [((0.0, 4.0), "A"), ((6.0, 10.0), "B")], None),    # 40% each of two
    ((0.0, 10.0), [((0.0, 3.0), "A"), ((3.0, 9.0), "B")], "B"),
])
def test_a_new_line_keeps_the_character_covering_more_than_half_of_it(new, old, kept):
    assert store.carried([new], old) == [kept]


@pytest.mark.asyncio
async def test_carry_over_compares_where_the_actor_spoke_not_the_moved_clip(db_sessionmaker):
    async with db_sessionmaker() as s:
        project = Project(name="P")
        s.add(project)
        await s.flush()
        job = Job(project_id=project.id, status=JobStatus.COMPLETED)
        uncle = Speaker(project_id=project.id, label="SPEAKER_02")
        s.add_all([job, uncle])
        await s.flush()
        # the actor spoke at 2–4 s; the user moved the clip to 10–12 s
        s.add(Segment(job_id=job.id, speaker_id=uncle.id, start_time=10.0, end_time=12.0, source_start=2.0,
                      source_end=4.0, who_confirmed=True))
        await s.commit()
        assert await store.kept_characters(s, job, [(2.1, 3.9), (10.1, 11.9)]) == ["SPEAKER_02", None]


# ── The analysis ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_known_characters_get_their_lines_before_translation(monkeypatch, uploads, background_db):
    project_id, ids = await _project(background_db)
    job_id = await _episode(background_db, project_id)
    _vocals(uploads, project_id, job_id)
    _transcribe(monkeypatch, [("SPEAKER_07", 0.0, 1.0, "你好"), ("SPEAKER_08", 2.0, 3.0, "再见")])
    _prints(monkeypatch, [
        LinePrints(voice(0), None, None, None),            # Lin Dong's voice, nobody on screen: sure
        LinePrints(voice(0), face(1), 0.8, b"jpeg"),       # Lin Dong's voice, Mei's speaking face: to check
    ])
    seen = {}
    _translate(monkeypatch, pipeline, seen)
    await pipeline.run_analysis_pipeline(job_id)

    assert seen["labels"] == ["SPEAKER_00", "SPEAKER_01"]
    assert "Lin Dong" in seen["cast"] and "Mei" in seen["cast"] and "SPEAKER_07" not in seen["cast"]
    (first, first_label), (second, second_label) = await _lines(background_db, job_id)
    assert (first_label, second_label) == ("SPEAKER_00", "SPEAKER_01")
    assert first.who_check is None
    assert json.loads(second.who_check) == [{"speaker_id": ids["SPEAKER_01"], "basis": "face"},
                                            {"speaker_id": ids["SPEAKER_00"], "basis": "voice"}]
    assert not first.who_confirmed and not second.who_confirmed
    async with background_db() as s:     # Gemini's labels made no new characters
        labels = (await s.execute(select(Speaker.label).where(Speaker.project_id == project_id))).scalars().all()
    assert sorted(labels) == ["SPEAKER_00", "SPEAKER_01"]
    assert (await _job(background_db, job_id)).status == JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_the_first_episode_keeps_gemini_speakers_and_gets_prints(monkeypatch, uploads, background_db):
    project_id, job_id = await _first_episode(background_db)
    _vocals(uploads, project_id, job_id)
    _transcribe(monkeypatch, [("SPEAKER_00", 0.0, 1.0, "你好"), ("SPEAKER_01", 2.0, 3.0, "再见")])
    calls = []
    _prints(monkeypatch, [LinePrints(voice(3), face(4), 0.9, b"\xff\xd8a face"),
                          LinePrints(voice(5), None, None, None)], calls)
    _translate(monkeypatch, pipeline)
    await pipeline.run_analysis_pipeline(job_id)

    assert calls == [[(0.0, 1.0), (2.0, 3.0)]]          # where the actor spoke
    rows = await _lines(background_db, job_id)
    assert [label for _, label in rows] == ["SPEAKER_00", "SPEAKER_01"]
    assert [seg.who_check for seg, _ in rows] == [None, None]
    kept = await _line_prints(background_db, job_id)
    first, second = kept[rows[0][0].id], kept[rows[1][0].id]
    assert {first.model_version, second.model_version} == {store.MODEL_VERSION}
    assert np.array_equal(np.frombuffer(first.voice_print, np.float32), voice(3))
    assert np.array_equal(np.frombuffer(first.face_print, np.float32), face(4))
    assert first.speaking_score == pytest.approx(0.9)
    assert Path(first.face_crop_path) == uploads / project_id / job_id / "faces" / f"{rows[0][0].id}.jpg"
    assert Path(first.face_crop_path).read_bytes() == b"\xff\xd8a face"
    assert second.face_print is None and second.face_crop_path is None
    assert len(store.MODEL_VERSION) == 12


@pytest.mark.asyncio
async def test_a_face_crop_that_cannot_be_written_never_fails_the_analysis(monkeypatch, uploads, background_db):
    project_id, job_id = await _first_episode(background_db)
    _vocals(uploads, project_id, job_id)
    (uploads / project_id / job_id / "faces").write_bytes(b"")     # a file where the crops folder should be
    _transcribe(monkeypatch, [("SPEAKER_00", 0.0, 1.0, "你好")])
    _prints(monkeypatch, [LinePrints(voice(3), face(4), 0.9, b"\xff\xd8a face")])
    _translate(monkeypatch, pipeline)
    await pipeline.run_analysis_pipeline(job_id)

    assert (await _job(background_db, job_id)).status == JobStatus.COMPLETED
    [kept] = (await _line_prints(background_db, job_id)).values()
    assert np.array_equal(np.frombuffer(kept.face_print, np.float32), face(4)) and kept.face_crop_path is None


@pytest.mark.asyncio
async def test_a_failed_analysis_leaves_no_face_crops(monkeypatch, uploads, background_db):
    project_id, job_id = await _first_episode(background_db)
    _vocals(uploads, project_id, job_id)
    _transcribe(monkeypatch, [("SPEAKER_00", 0.0, 1.0, "你好")])
    _prints(monkeypatch, [LinePrints(voice(3), face(4), 0.9, b"\xff\xd8a face")])
    _translate(monkeypatch, pipeline)

    async def broken(*args, **kwargs):      # after the prints were saved, before the commit
        raise RuntimeError("database is locked")

    monkeypatch.setattr(pipeline, "remove_unused_auto_speakers", broken)
    await pipeline.run_analysis_pipeline(job_id)

    assert (await _job(background_db, job_id)).status == JobStatus.FAILED
    assert await _line_prints(background_db, job_id) == {}
    faces = uploads / project_id / job_id / "faces"
    assert not faces.exists() or list(faces.iterdir()) == []


@pytest.mark.asyncio
async def test_analysis_without_vocals_keeps_gemini_speakers(monkeypatch, uploads, background_db, caplog):
    project_id, _ids = await _project(background_db)
    job_dir = uploads / project_id
    job_id = await _episode(background_db, project_id)
    audio = job_dir / job_id / "audio.wav"            # separation never ran: only the extracted audio
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"RIFFfake")
    async with background_db() as s:
        (await s.get(Job, job_id)).audio_path = str(audio)
        await s.commit()
    _transcribe(monkeypatch, [("SPEAKER_07", 0.0, 1.0, "你好")])
    calls = []
    _prints(monkeypatch, [LinePrints(voice(0), None, None, None)], calls)
    _translate(monkeypatch, pipeline)
    with caplog.at_level(logging.WARNING, logger="app.services.characters.store"):
        await pipeline.run_analysis_pipeline(job_id)

    assert calls == []
    rows = await _lines(background_db, job_id)
    assert [(label, seg.who_check) for seg, label in rows] == [("SPEAKER_07", None)]
    assert await _line_prints(background_db, job_id) == {}
    assert (await _job(background_db, job_id)).status == JobStatus.COMPLETED
    [warning] = _store_warnings(caplog)
    assert "vocals.wav" in warning.getMessage()


@pytest.mark.parametrize("error, says", [
    (RuntimeError("ffmpeg could not read the video"), "failed"),
    (OSError(24, "Too many open files"), "failed"),          # not "can't run here": the Mac can run them
    (ImportError("onnxruntime needs macOS 13.4"), "can't run here"),
    (models.ModelMissing("face_recognition_sface_2021dec.onnx is missing"), "can't run here"),
])
@pytest.mark.asyncio
async def test_recognition_that_fails_keeps_gemini_speakers(error, says, monkeypatch, uploads, background_db,
                                                            caplog):
    project_id, _ids = await _project(background_db)
    job_id = await _episode(background_db, project_id)
    _vocals(uploads, project_id, job_id)
    _transcribe(monkeypatch, [("SPEAKER_07", 0.0, 1.0, "你好"), ("SPEAKER_00", 2.0, 3.0, "再见")])
    _prints(monkeypatch, error)
    _translate(monkeypatch, pipeline)
    with caplog.at_level(logging.WARNING, logger="app.services.characters.store"):
        await pipeline.run_analysis_pipeline(job_id)

    rows = await _lines(background_db, job_id)
    assert [(label, seg.who_check) for seg, label in rows] == [("SPEAKER_07", None), ("SPEAKER_00", None)]
    assert await _line_prints(background_db, job_id) == {}
    job = await _job(background_db, job_id)
    assert job.status == JobStatus.COMPLETED and job.error_msg == ""
    [warning] = _store_warnings(caplog)
    assert str(error) in warning.getMessage() and says in warning.getMessage()
    assert warning.exc_info is not None and warning.exc_info[1] is error     # the traceback is in the log


@pytest.mark.parametrize("cannot", ["raises", "no vocals"])
@pytest.mark.asyncio
async def test_a_confirmed_character_is_kept_when_recognition_cannot_run(cannot, monkeypatch, uploads,
                                                                         background_db):
    project_id, _ids = await _project(background_db)
    job_id = await _episode(background_db, project_id, status=JobStatus.COMPLETED)
    uncle = await _old_confirmed_line(background_db, project_id, job_id)
    if cannot == "raises":
        _vocals(uploads, project_id, job_id)
    else:
        audio = uploads / project_id / job_id / "audio.wav"
        audio.parent.mkdir(parents=True)
        audio.write_bytes(b"RIFFfake")
        async with background_db() as s:
            (await s.get(Job, job_id)).audio_path = str(audio)
            await s.commit()
    _transcribe(monkeypatch, [("SPEAKER_09", 2.1, 3.9, "新")])
    _prints(monkeypatch, RuntimeError("ffmpeg died"))
    _translate(monkeypatch, pipeline)
    await pipeline.run_analysis_pipeline(job_id)

    [(line, label)] = await _lines(background_db, job_id)
    assert (line.speaker_id, label, line.who_confirmed, line.who_check) == (uncle, "SPEAKER_02", True, None)


@pytest.mark.asyncio
async def test_status_goes_recognizing_between_transcribing_and_translating(monkeypatch, uploads, file_db):
    project_id, _ids = await _project(file_db)
    job_id = await _episode(file_db, project_id)
    _vocals(uploads, project_id, job_id)
    _transcribe(monkeypatch, [("SPEAKER_00", 0.0, 1.0, "你好")])
    statuses = []
    update = pipeline._update_job

    async def record(db, job, status, progress, error_msg=""):
        statuses.append((status, progress))
        await update(db, job, status, progress, error_msg)

    monkeypatch.setattr(pipeline, "_update_job", record)
    during = []
    pool = file_db.kw["bind"].pool      # the app's own pool: one connection

    def prints_while(video_path, vocals_path, spans, with_faces=True):
        # Recorded, not asserted here: recognition would swallow the AssertionError and carry on
        during.append((statuses[-1][0], pool.checkedout()))
        return [LinePrints(voice(0), None, None, None)]

    monkeypatch.setattr(prints_module, "prints_for_spans", prints_while)
    _translate(monkeypatch, pipeline)
    await pipeline.run_analysis_pipeline(job_id)

    order = [status for status, _ in statuses]
    assert order.index(JobStatus.TRANSCRIBING) < order.index(JobStatus.RECOGNIZING) < order.index(JobStatus.TRANSLATING)
    assert during == [(JobStatus.RECOGNIZING, 0)]     # the models ran with no connection held
    progress = [p for _, p in statuses]
    assert progress == sorted(progress)


@pytest.mark.asyncio
async def test_a_line_still_to_check_never_decides_what_a_character_sounds_like(monkeypatch, uploads,
                                                                                background_db):
    project_id, ids = await _project(background_db)
    job_id = await _episode(background_db, project_id)
    _vocals(uploads, project_id, job_id)
    _transcribe(monkeypatch, [("SPEAKER_07", 0.0, 1.0, "你好", "man"), ("SPEAKER_08", 2.0, 3.0, "再见", "woman")])
    near = voice(0) + 0.95 * voice(1)
    _prints(monkeypatch, [LinePrints(voice(0), None, None, None),                            # Lin Dong, sure
                          LinePrints(near / np.linalg.norm(near), None, None, None)])        # Lin Dong? to check
    seen = {}
    _translate(monkeypatch, pipeline, seen)
    await pipeline.run_analysis_pipeline(job_id)

    assert seen["labels"] == ["SPEAKER_00", "SPEAKER_00"]
    [lin] = [row for row in seen["cast"].splitlines() if row.startswith("- SPEAKER_00:")]
    assert "probably a man" in lin
    async with background_db() as s:
        assert (await s.get(Speaker, ids["SPEAKER_00"])).gender == Gender.MALE


@pytest.mark.asyncio
async def test_khmer_machine_is_the_translation_written(monkeypatch, uploads, background_db):
    project_id, job_id = await _first_episode(background_db)
    _vocals(uploads, project_id, job_id)
    _transcribe(monkeypatch, [("SPEAKER_00", 0.0, 1.0, "一"), ("SPEAKER_00", 2.0, 3.0, "二"),
                              ("SPEAKER_00", 4.0, 5.0, "三"), ("SPEAKER_00", 6.0, 7.0, "四")])
    _prints(monkeypatch, [LinePrints(None, None, None, None)] * 4)
    _translate(monkeypatch, pipeline, texts=["km:一", "", "", "km:四"])
    await pipeline.run_analysis_pipeline(job_id)
    rows = [seg for seg, _ in await _lines(background_db, job_id)]
    assert [s.khmer_machine for s in rows] == ["km:一", "", "", "km:四"]

    async with background_db() as s:      # the user edits a translated line
        (await s.get(Segment, rows[3].id)).khmer_text = "edited by hand"
        await s.commit()

    async def type_into_third_line():      # ... and types the third one while Translate missing lines runs
        async with background_db() as s:
            (await s.get(Segment, rows[2].id)).khmer_text = "typed meanwhile"
            await s.commit()

    _translate(monkeypatch, pipeline, texts=["km:二", "km:三"], meanwhile=type_into_third_line)
    await pipeline.run_translation(job_id)
    rows = [seg for seg, _ in await _lines(background_db, job_id)]
    assert [(s.khmer_text, s.khmer_machine) for s in rows] == [
        ("km:一", "km:一"), ("km:二", "km:二"), ("typed meanwhile", ""), ("edited by hand", "km:四"),
    ]


@pytest.mark.asyncio
async def test_reanalysis_keeps_confirmed_characters_on_overlapping_lines(monkeypatch, uploads, background_db):
    project_id, ids = await _project(background_db)
    job_id = await _episode(background_db, project_id, status=JobStatus.COMPLETED)
    async with background_db() as s:
        c = Speaker(project_id=project_id, label="SPEAKER_02", display_name="Uncle Wang")
        s.add(c)
        await s.flush()
        old = Segment(job_id=job_id, speaker_id=c.id, start_time=2.0, end_time=4.0, source_start=2.0,
                      source_end=4.0, source_text="旧", khmer_text="km", who_confirmed=True)
        unsure = Segment(job_id=job_id, speaker_id=c.id, start_time=6.0, end_time=7.0, source_text="旧二",
                         khmer_text="km", who_check="[]")
        s.add_all([old, unsure])
        await s.flush()
        # This episode's own old lines never vote: they're being replaced (Uncle Wang = voice 2)
        s.add(LinePrint(segment_id=old.id, job_id=job_id, voice_print=voice(2).tobytes(),
                        model_version=store.MODEL_VERSION))
        await s.commit()
        c_id = c.id
    _vocals(uploads, project_id, job_id)
    _transcribe(monkeypatch, [("SPEAKER_09", 2.1, 3.9, "新"), ("SPEAKER_09", 6.0, 7.0, "新二")])
    _prints(monkeypatch, [LinePrints(voice(0), None, None, None),       # sounds like Lin Dong
                          LinePrints(voice(2), None, None, None)])      # Uncle Wang's old line's voice
    seen = {}
    _translate(monkeypatch, pipeline, seen)
    await pipeline.run_analysis_pipeline(job_id)

    assert seen["labels"] == ["SPEAKER_02", "SPEAKER_09"]
    (kept, kept_label), (other, other_label) = await _lines(background_db, job_id)
    assert (kept.source_text, kept.speaker_id, kept_label) == ("新", c_id, "SPEAKER_02")
    assert kept.who_confirmed is True and kept.who_check is None
    # The unconfirmed old line kept nothing, and Uncle Wang's old print didn't vote: nobody known, to check
    assert (other_label, other.who_confirmed) == ("SPEAKER_09", False)
    assert {(g["speaker_id"], g["basis"]) for g in json.loads(other.who_check)} == {
        (ids["SPEAKER_00"], "voice"), (ids["SPEAKER_01"], "voice")}
    assert set(await _line_prints(background_db, job_id)) == {kept.id, other.id}   # fresh prints, old ones gone


@pytest.mark.asyncio
async def test_a_line_without_prints_is_to_check_with_no_guesses(client, monkeypatch, uploads, background_db):
    project_id, _ids = await _project(background_db)
    job_id = await _episode(background_db, project_id)
    _vocals(uploads, project_id, job_id)
    _transcribe(monkeypatch, [("SPEAKER_07", 0.0, 0.2, "嗯")])
    _prints(monkeypatch, [LinePrints(None, None, None, None)])      # too short for a voice print
    _translate(monkeypatch, pipeline)
    await pipeline.run_analysis_pipeline(job_id)

    [(line, label)] = await _lines(background_db, job_id)
    assert (label, line.who_check) == ("SPEAKER_07", "[]")         # nobody: a new character, to check
    got = (await client.get(f"/api/v1/jobs/{job_id}/segments")).json()
    assert got[0]["who_check"] == []
    # an undo puts back "to check, no guesses" as it was
    await client.patch(f"/api/v1/segments/{line.id}", json={"who_check": None})
    resp = await client.patch(f"/api/v1/segments/{line.id}", json={"who_check": []})
    assert resp.json()["who_check"] == []


# ── The subtitle analysis ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_subtitle_analysis_recognises_too(monkeypatch, uploads, background_db):
    project_id, ids = await _project(background_db)
    job_id = await _episode(background_db, project_id, subtitle_path="subs.srt")
    async with background_db() as s:
        lines = [Segment(job_id=job_id, start_time=t, end_time=t + 1.0, source_start=t, source_end=t + 1.0,
                         source_text=f"字幕{i}", khmer_text="") for i, t in enumerate((0.0, 2.0, 4.0))]
        lines[2].speaker_id, lines[2].who_confirmed = ids["SPEAKER_00"], True     # the user's pick stays
        s.add_all(lines)
        await s.flush()
        s.add(LinePrint(segment_id=lines[0].id, job_id=job_id, voice_print=voice(9).tobytes(), model_version="old"))
        await s.commit()
    _vocals(uploads, project_id, job_id)

    async def label_speech(*args, **kwargs):
        return ["SPEAKER_05", "SPEAKER_05", "SPEAKER_05"], [], False

    monkeypatch.setattr(subtitle_pipeline, "label_speech", label_speech)
    calls = []
    _prints(monkeypatch, [LinePrints(voice(0), None, None, None),            # Lin Dong, sure
                          LinePrints(voice(0), face(1), 0.7, None),          # Mei's face, Lin Dong's voice
                          LinePrints(voice(1), None, None, None)], calls)    # sounds like Mei, but confirmed
    seen = {}
    _translate(monkeypatch, subtitle_pipeline, seen)
    await subtitle_pipeline.run_subtitle_analysis_pipeline(job_id)

    assert calls == [[(0.0, 1.0), (2.0, 3.0), (4.0, 5.0)]]
    assert seen["labels"] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00"]
    rows = await _lines(background_db, job_id)
    assert [label for _, label in rows] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00"]
    assert rows[0][0].who_check is None and rows[2][0].who_check is None and rows[2][0].who_confirmed
    assert json.loads(rows[1][0].who_check) == [{"speaker_id": ids["SPEAKER_01"], "basis": "face"},
                                                {"speaker_id": ids["SPEAKER_00"], "basis": "voice"}]
    assert [seg.khmer_machine for seg, _ in rows] == ["km:字幕0", "km:字幕1", "km:字幕2"]
    kept = await _line_prints(background_db, job_id)
    assert set(kept) == {seg.id for seg, _ in rows}
    assert {row.model_version for row in kept.values()} == {store.MODEL_VERSION}
    assert np.array_equal(np.frombuffer(kept[rows[0][0].id].voice_print, np.float32), voice(0))
    assert (await _job(background_db, job_id)).status == JobStatus.COMPLETED



# ── One episode at a time ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_two_analyses_never_run_the_models_at_once(monkeypatch, uploads, file_db):
    project_id, _ids = await _project(file_db)
    jobs = [await _episode(file_db, project_id) for _ in range(2)]
    for job_id in jobs:
        _vocals(uploads, project_id, job_id)
    running, most = [], []

    def slow_prints(video_path, vocals_path, spans, with_faces=True):
        running.append(1)
        most.append(len(running))
        time.sleep(0.1)
        running.pop()
        return [LinePrints(voice(0), None, None, None)]

    monkeypatch.setattr(prints_module, "prints_for_spans", slow_prints)

    async def recognise(job_id):
        async with file_db() as s:
            return await store.recognise(s, await s.get(Job, job_id), [(0.0, 1.0)], ["SPEAKER_07"])

    results = await asyncio.gather(*(recognise(job_id) for job_id in jobs))
    assert max(most) == 1
    assert [labels for labels, _, _ in results] == [["SPEAKER_00"], ["SPEAKER_00"]]
