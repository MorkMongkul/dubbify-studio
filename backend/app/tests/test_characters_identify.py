"""
Detect speakers (0.5.0) checks an episode's lines against the project's characters, locally: no Gemini call, and
no text, timing or confirmed line is touched. Without characters yet (a project's first episode), or when the
models can't run, it is the Gemini re-detection it was — which now also leaves confirmed lines alone.

Gemini and the prints are replaced at the service boundary (pipeline.transcribe_audio,
characters.prints.prints_for_spans); the profiles come from real stored prints, as in test_characters_pipeline.
"""
import asyncio
import json
from datetime import datetime

import numpy as np
import pytest
from sqlalchemy import select

from app.models.models import Job, JobStatus, LinePrint, Segment, Speaker
from app.services import pipeline
from app.services.characters import models, store
from app.services.characters import prints as prints_module
from app.services.characters.types import LinePrints
from app.services.diarizer import DiarizedSegment
from app.tests.test_characters_pipeline import (  # noqa: F401  (fixtures)
    _episode, _first_episode, _line_prints, _lines, _old_confirmed_line, _prints, _project, _store_warnings, _transcribe, _vocals,
    _waiting_line, background_db, face, uploads, voice,
)


def _p(k: int | None, with_face: bool = True) -> LinePrints:
    """Prints that match character k exactly (k=None: a voice nobody has)."""
    return LinePrints(voice(5 if k is None else k), face(k) if with_face and k is not None else None,
                      0.9 if with_face and k is not None else None, None)


async def _detect_setup(background_db, uploads, lines, vocals=True):
    """The project's characters plus a finished second episode with `lines`: (start, end, fields)."""
    project_id, ids = await _project(background_db)
    job_id = await _episode(background_db, project_id, status=JobStatus.COMPLETED)
    async with background_db() as s:
        rows = []
        for k, (start, end, fields) in enumerate(lines):
            rows.append(Segment(job_id=job_id, start_time=start, end_time=end, source_start=start, source_end=end,
                                source_text=f"旧{k}", khmer_text=f"km{k}", **fields))
        s.add_all(rows)
        await s.commit()
        segment_ids = [r.id for r in rows]
    if vocals:
        _vocals(uploads, project_id, job_id)
    return project_id, job_id, ids, segment_ids


async def _detect(client, job_id):
    resp = await client.post(f"/api/v1/jobs/{job_id}/detect-speakers")
    assert resp.status_code == 202


def _no_gemini(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("Gemini was called")
    monkeypatch.setattr(pipeline, "transcribe_audio", forbidden)


async def _store_row(background_db, job_id, segment_id, k, version=None):
    async with background_db() as s:
        s.add(LinePrint(segment_id=segment_id, job_id=job_id, voice_print=voice(k).tobytes(),
                        face_print=face(k).tobytes(), speaking_score=0.9,
                        model_version=version or store.MODEL_VERSION))
        await s.commit()


@pytest.mark.asyncio
async def test_detect_speakers_never_calls_gemini(client, monkeypatch, uploads, background_db):
    project_id, job_id, ids, _ = await _detect_setup(background_db, uploads, [(0.0, 2.0, {}), (3.0, 5.0, {})])
    _no_gemini(monkeypatch)
    _prints(monkeypatch, [_p(1), _p(0)])
    await _detect(client, job_id)

    lines = await _lines(background_db, job_id)
    assert [label for _, label in lines] == ["SPEAKER_01", "SPEAKER_00"]
    assert [seg.who_check for seg, _ in lines] == [None, None]
    assert [(seg.source_text, seg.khmer_text, seg.start_time) for seg, _ in lines] == [
        ("旧0", "km0", 0.0), ("旧1", "km1", 3.0)]
    job = await store_job(background_db, job_id)
    assert job.status == JobStatus.COMPLETED and job.progress == 100 and not job.error_msg
    assert len(await _line_prints(background_db, job_id)) == 2   # kept for the next episode


async def store_job(background_db, job_id) -> Job:
    async with background_db() as s:
        return await s.get(Job, job_id)


@pytest.mark.asyncio
async def test_detect_speakers_never_changes_a_confirmed_line(client, monkeypatch, uploads, background_db):
    project_id, job_id, ids, _ = await _detect_setup(background_db, uploads, [
        (0.0, 2.0, {"who_confirmed": True}), (3.0, 5.0, {"tts_audio_path": "tts/a.wav"}), (6.0, 8.0, {})])
    uncle = await _old_confirmed_line(background_db, project_id, job_id)
    async with background_db() as s:   # the confirmed and the voiced line stand on Uncle Wang, the open one on nobody
        for seg in (await s.execute(select(Segment).where(Segment.job_id == job_id,
                                                          Segment.source_text.in_(["旧0", "旧1"])))).scalars():
            seg.speaker_id = uncle
        await s.commit()
    _no_gemini(monkeypatch)
    calls = []
    _prints(monkeypatch, [_p(0)], calls)
    await _detect(client, job_id)

    lines = {seg.source_text: (label, seg.who_check) for seg, label in await _lines(background_db, job_id)}
    assert lines["旧0"] == ("SPEAKER_02", None) and lines["旧1"] == ("SPEAKER_02", None)
    assert lines["旧2"] == ("SPEAKER_00", None)
    assert calls == [[(6.0, 8.0)]]   # the models heard only the open line


@pytest.mark.asyncio
async def test_detect_speakers_marks_uncertain_lines(client, monkeypatch, uploads, background_db):
    project_id, job_id, ids, _ = await _detect_setup(background_db, uploads, [(0.0, 2.0, {})])
    _no_gemini(monkeypatch)
    between = (voice(0) + voice(1)) / np.sqrt(2)
    _prints(monkeypatch, [LinePrints(between.astype(np.float32), None, None, None)])
    await _detect(client, job_id)

    [(seg, label)] = await _lines(background_db, job_id)
    guesses = json.loads(seg.who_check)
    assert {g["speaker_id"] for g in guesses} == set(ids.values()) and {g["basis"] for g in guesses} == {"voice"}
    assert label in ids   # the better guess, to be checked by the user


@pytest.mark.asyncio
async def test_detect_speakers_is_never_sure_by_voice_over_another_line(client, monkeypatch, uploads, background_db):
    """An open line spoken over a confirmed one of the same episode has a mixed voice print: to check."""
    project_id, job_id, ids, _ = await _detect_setup(background_db, uploads, [
        (0.0, 2.0, {}), (1.0, 3.0, {"who_confirmed": True}), (5.0, 6.0, {})])
    _no_gemini(monkeypatch)
    _prints(monkeypatch, [_p(0, with_face=False), _p(0, with_face=False)])
    await _detect(client, job_id)

    lines = {seg.source_text: (label, seg.who_check) for seg, label in await _lines(background_db, job_id)}
    label, check = lines["旧0"]
    assert label == "SPEAKER_00" and [g["basis"] for g in json.loads(check)] == ["voice", "voice"]
    assert lines["旧2"] == ("SPEAKER_00", None)


@pytest.mark.asyncio
async def test_detect_speakers_is_never_sure_while_confirmed_lines_wait_for_prints(client, monkeypatch, uploads,
                                                                                  background_db):
    project_id, job_id, ids, _ = await _detect_setup(background_db, uploads, [(0.0, 2.0, {})])
    await _waiting_line(background_db, uploads, project_id, ids["SPEAKER_01"])
    _no_gemini(monkeypatch)
    _prints(monkeypatch, [_p(0)])    # Lin Dong's voice (and face, which no longer counts)
    await _detect(client, job_id)

    [(seg, label)] = await _lines(background_db, job_id)
    assert label == "SPEAKER_00"
    assert json.loads(seg.who_check) == [{"speaker_id": ids["SPEAKER_00"], "basis": "voice"},
                                         {"speaker_id": ids["SPEAKER_01"], "basis": "voice"}]


@pytest.mark.asyncio
async def test_detect_speakers_keeps_a_nobody_line_on_its_speaker(client, monkeypatch, uploads, background_db):
    project_id, job_id, ids, _ = await _detect_setup(background_db, uploads, [(0.0, 2.0, {})])
    uncle = await _old_confirmed_line(background_db, project_id, job_id)
    async with background_db() as s:
        (await s.execute(select(Segment).where(Segment.source_text == "旧0"))).scalar_one().speaker_id = uncle
        await s.commit()
    _no_gemini(monkeypatch)
    _prints(monkeypatch, [_p(None)])
    await _detect(client, job_id)

    lines = {seg.source_text: (label, seg.who_check) for seg, label in await _lines(background_db, job_id)}
    label, check = lines["旧0"]
    assert label == "SPEAKER_02" and check is not None   # still Uncle Wang, now marked to check


@pytest.mark.asyncio
async def test_detect_speakers_computes_only_missing_prints(client, monkeypatch, uploads, background_db):
    project_id, job_id, ids, seg_ids = await _detect_setup(
        background_db, uploads, [(0.0, 2.0, {}), (3.0, 5.0, {}), (6.0, 8.0, {})])
    await _store_row(background_db, job_id, seg_ids[1], 1)   # the middle line has today's prints
    _no_gemini(monkeypatch)
    calls = []
    _prints(monkeypatch, [_p(0), _p(0)], calls)
    await _detect(client, job_id)

    assert calls == [[(0.0, 2.0), (6.0, 8.0)]]
    assert [label for _, label in await _lines(background_db, job_id)] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00"]
    assert set(await _line_prints(background_db, job_id)) == set(seg_ids)


@pytest.mark.asyncio
async def test_detect_speakers_recomputes_stale_prints(client, monkeypatch, uploads, background_db):
    project_id, job_id, ids, seg_ids = await _detect_setup(background_db, uploads, [(0.0, 2.0, {})])
    await _store_row(background_db, job_id, seg_ids[0], 1, version="older-models")
    _no_gemini(monkeypatch)
    calls = []
    _prints(monkeypatch, [_p(0)], calls)
    await _detect(client, job_id)

    assert calls == [[(0.0, 2.0)]]
    row = (await _line_prints(background_db, job_id))[seg_ids[0]]
    assert row.model_version == store.MODEL_VERSION
    assert np.array_equal(np.frombuffer(row.voice_print, np.float32), voice(0))
    assert [label for _, label in await _lines(background_db, job_id)] == ["SPEAKER_00"]


@pytest.mark.asyncio
@pytest.mark.parametrize("why", ["no vocals", "models missing", "prints fail"])
async def test_detect_speakers_without_vocals_uses_gemini_and_keeps_confirmed_lines(
        client, monkeypatch, uploads, background_db, caplog, why):
    project_id, job_id, ids, _ = await _detect_setup(
        background_db, uploads, [(0.0, 2.0, {"who_confirmed": True}), (3.0, 5.0, {})], vocals=why != "no vocals")
    if why == "no vocals":   # the extracted audio is all there is
        audio = uploads / "audio.wav"
        audio.write_bytes(b"RIFFfake")
        async with background_db() as s:
            (await s.get(Job, job_id)).audio_path = str(audio)
            await s.commit()
    elif why == "models missing":
        _prints(monkeypatch, models.ModelMissing("no models"))
    else:
        _prints(monkeypatch, OSError("ffmpeg missing"))
    _transcribe(monkeypatch, [("SPEAKER_09", 0.0, 2.0, "旧0"), ("SPEAKER_09", 3.0, 5.0, "旧1")])
    with caplog.at_level("WARNING", logger="app.services.characters.store"):
        await _detect(client, job_id)

    lines = {seg.source_text: label for seg, label in await _lines(background_db, job_id)}
    assert lines["旧0"] is None   # confirmed: Gemini's label doesn't relabel it
    assert lines["旧1"] == "SPEAKER_09"
    assert _store_warnings(caplog)
    assert (await store_job(background_db, job_id)).status == JobStatus.COMPLETED


def _while_the_models_run(monkeypatch, result, edit):
    """prints_for_spans that, in the middle of its run, lets `edit(session)` change the database from another
    session (as the user does while the models take their minutes)."""
    loop = asyncio.get_running_loop()

    def fake(video_path, vocals_path, spans, with_faces=True):
        async def user_edit():
            async with edit.sessionmaker() as s:
                await edit.work(s)
                await s.commit()
        asyncio.run_coroutine_threadsafe(user_edit(), loop).result(timeout=10)
        return list(result)
    monkeypatch.setattr(prints_module, "prints_for_spans", fake)


class _Edit:
    def __init__(self, sessionmaker, work):
        self.sessionmaker, self.work = sessionmaker, work


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["picked", "voiced"])
async def test_a_line_confirmed_while_the_models_run_is_not_relabelled(
        client, monkeypatch, uploads, background_db, how):
    project_id, job_id, ids, seg_ids = await _detect_setup(background_db, uploads, [(0.0, 2.0, {})])
    uncle = await _old_confirmed_line(background_db, project_id, job_id)
    async with background_db() as s:
        (await s.get(Segment, seg_ids[0])).speaker_id = uncle
        await s.commit()

    async def confirm(s):
        seg = await s.get(Segment, seg_ids[0])
        if how == "picked":
            seg.who_confirmed = True
        else:
            seg.tts_audio_path = "tts/a.wav"
    _no_gemini(monkeypatch)
    _while_the_models_run(monkeypatch, [_p(0)], _Edit(background_db, confirm))
    await _detect(client, job_id)

    lines = {seg.source_text: (label, seg.who_check) for seg, label in await _lines(background_db, job_id)}
    assert lines["旧0"] == ("SPEAKER_02", None)   # still Uncle Wang, not Lin Dong


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["picked", "episode finished"])
async def test_a_line_confirmed_while_gemini_listens_is_not_relabelled(client, monkeypatch, uploads, background_db,
                                                                        how):
    """Without characters yet, Detect speakers asks Gemini, which takes minutes: a line confirmed meanwhile — its
    character picked, or its episode marked finished — keeps its speaker (Ruling U)."""
    project_id = (await client.post("/api/v1/projects/", json={"name": "New series"})).json()["id"]
    job_id = await _episode(background_db, project_id, status=JobStatus.COMPLETED)
    async with background_db() as s:
        uncle = Speaker(project_id=project_id, label="SPEAKER_02", display_name="Uncle Wang")
        s.add(uncle)
        await s.flush()
        line = Segment(job_id=job_id, speaker_id=uncle.id, start_time=0.0, end_time=2.0, source_start=0.0,
                       source_end=2.0, source_text="旧", khmer_text="km")
        s.add(line)
        await s.commit()
        line_id = line.id
    _vocals(uploads, project_id, job_id)

    async def gemini_listens(audio_path, references=(), taken_labels=(), language="zh", clip_audio=None):
        async with background_db() as s:   # the user confirms the line meanwhile
            if how == "picked":
                (await s.get(Segment, line_id)).who_confirmed = True
            else:
                (await s.get(Job, job_id)).finished_at = datetime.now()
            await s.commit()
        return [DiarizedSegment("SPEAKER_09", 0.0, 2.0, "unknown", "adult", "旧")], False
    monkeypatch.setattr(pipeline, "transcribe_audio", gemini_listens)
    await _detect(client, job_id)

    [(seg, label)] = await _lines(background_db, job_id)
    assert label == "SPEAKER_02"
    assert (await store_job(background_db, job_id)).status == JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_no_gemini_key_and_no_models_says_so(client, monkeypatch, uploads, background_db):
    project_id, job_id, ids, _ = await _detect_setup(background_db, uploads, [(0.0, 2.0, {})])
    monkeypatch.setattr(pipeline.settings, "GEMINI_API_KEY", "")
    _no_gemini(monkeypatch)
    _prints(monkeypatch, models.ModelMissing("no models"))
    await _detect(client, job_id)

    job = await store_job(background_db, job_id)
    assert job.status == JobStatus.COMPLETED   # its lines are still there
    assert "can't recognise voices on this Mac" in job.error_msg and "Gemini key" in job.error_msg


@pytest.mark.asyncio
async def test_no_gemini_key_and_no_vocals_names_the_vocals(monkeypatch, uploads, background_db):
    project_id, job_id, ids, _ = await _detect_setup(background_db, uploads, [(0.0, 2.0, {})], vocals=False)
    audio = uploads / "audio.wav"
    audio.write_bytes(b"RIFFfake")
    async with background_db() as s:   # extracted audio exists, the separated vocals don't
        (await s.get(Job, job_id)).audio_path = str(audio)
        await s.commit()
    monkeypatch.setattr(pipeline.settings, "GEMINI_API_KEY", "")
    _no_gemini(monkeypatch)
    await pipeline.run_speaker_detection(job_id)

    job = await store_job(background_db, job_id)
    assert job.status == JobStatus.COMPLETED
    assert "weren't separated" in job.error_msg and "Gemini key" in job.error_msg
    assert "on this Mac" not in job.error_msg


@pytest.mark.asyncio
async def test_no_gemini_key_and_no_characters_is_not_blamed_on_this_mac(monkeypatch, uploads, background_db):
    project_id, job_id = await _first_episode(background_db)
    async with background_db() as s:
        s.add(Segment(job_id=job_id, start_time=0.0, end_time=2.0, source_text="旧", khmer_text="km"))
        await s.commit()
    _vocals(uploads, project_id, job_id)
    monkeypatch.setattr(pipeline.settings, "GEMINI_API_KEY", "")
    _transcribe(monkeypatch, [("SPEAKER_00", 0.0, 2.0, "旧")])
    await pipeline.run_speaker_detection(job_id)   # directly: the route would have refused with a 400

    assert "on this Mac" not in (await store_job(background_db, job_id)).error_msg
