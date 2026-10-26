"""
Existing projects become characters (0.5.0). Before characters, the owner re-used a label for another actor by
changing its voice, or changed one line's own Khmer voice; the upgrade puts each older line on the character of the
voice it was made with (its own, else the one its audio sounds like), once per project, in the background before that
project's prints, and computes the prints of every older line so profiles exist when it ends.

The models are replaced at the service boundary (characters.prints.prints_for_spans, characters.voiceprint.file_print),
as in test_characters_pipeline.
"""
import asyncio
import logging
import os
import platform
import subprocess
import sys
from datetime import datetime

import numpy as np
import pytest
from sqlalchemy import select

from app.core.binaries import ffmpeg

from app.models.models import Job, JobStatus, LinePrint, Project, Segment, Speaker, Voice
from app.services.characters import store, upgrade
from app.services.characters.types import LinePrints
from app.services.characters import prints as prints_module
from app.tests.test_characters_pipeline import (  # noqa: F401  (fixtures)
    _episode, _line_prints, _prints, _project, _vocals, background_db, face, uploads, voice,
)


async def _voices(db_sessionmaker, n=3) -> list[str]:
    async with db_sessionmaker() as s:
        rows = [Voice(name=f"V{i}", mode="design") for i in range(n)]
        s.add_all(rows)
        await s.commit()
        return [r.id for r in rows]


async def _add_lines(db_sessionmaker, job_id, lines) -> list[str]:
    """lines: (speaker id or None, own voice id or None, extra fields)."""
    async with db_sessionmaker() as s:
        rows = []
        for k, (speaker_id, voice_id, fields) in enumerate(lines):
            rows.append(Segment(job_id=job_id, speaker_id=speaker_id, voice_id=voice_id, start_time=k * 3.0,
                                end_time=k * 3.0 + 2.0, source_start=k * 3.0, source_end=k * 3.0 + 2.0,
                                source_text=f"句{k}", khmer_text=f"km{k}", **fields))
        s.add_all(rows)
        await s.commit()
        return [r.id for r in rows]


async def _speaker(db_sessionmaker, project_id, label, voice_id=None, name="") -> str:
    async with db_sessionmaker() as s:
        sp = Speaker(project_id=project_id, label=label, display_name=name, voice_id=voice_id)
        s.add(sp)
        await s.commit()
        return sp.id


async def _seg(db_sessionmaker, segment_id) -> Segment:
    async with db_sessionmaker() as s:
        return await s.get(Segment, segment_id)


async def _convert(db_sessionmaker, project_id) -> int:
    async with db_sessionmaker() as s:
        moved = await upgrade.convert_voice_overrides(s, project_id)
        await s.commit()
        return moved


async def _setup(db_sessionmaker):
    """A project from before 0.5.0 with Lin Dong (SPEAKER_00) and Mei (SPEAKER_01), and one episode (no lines yet)."""
    project_id, ids = await _project(db_sessionmaker)
    async with db_sessionmaker() as s:
        (await s.get(Project, project_id)).characters_version = None   # NULL, as add_missing_columns leaves it
        await s.commit()
    job_id = await _episode(db_sessionmaker, project_id, status=JobStatus.COMPLETED)
    return project_id, ids, job_id


@pytest.mark.asyncio
async def test_a_line_voiced_by_another_characters_voice_moves_to_that_character(db_sessionmaker):
    project_id, ids, job_id = await _setup(db_sessionmaker)
    v = await _voices(db_sessionmaker)
    async with db_sessionmaker() as s:
        (await s.get(Speaker, ids["SPEAKER_01"])).voice_id = v[1]
        (await s.get(Speaker, ids["SPEAKER_00"])).voice_id = v[0]
        await s.commit()
    own, line = await _add_lines(db_sessionmaker, job_id, [
        (ids["SPEAKER_00"], None, {}),
        (ids["SPEAKER_00"], v[1], {"who_check": "[]"}),   # labelled Lin Dong, but the owner gave it Mei's voice
    ])

    assert await _convert(db_sessionmaker, project_id) == 1

    moved = await _seg(db_sessionmaker, line)
    assert (moved.speaker_id, moved.voice_id, moved.who_confirmed, moved.who_check) == (
        ids["SPEAKER_01"], None, True, None)
    untouched = await _seg(db_sessionmaker, own)
    assert (untouched.speaker_id, untouched.who_confirmed) == (ids["SPEAKER_00"], False)


@pytest.mark.asyncio
async def test_with_several_characters_on_that_voice_the_one_with_most_lines_wins(db_sessionmaker):
    project_id, ids, job_id = await _setup(db_sessionmaker)
    v = await _voices(db_sessionmaker)
    few = await _speaker(db_sessionmaker, project_id, "SPEAKER_02", v[2])
    many = await _speaker(db_sessionmaker, project_id, "SPEAKER_03", v[2])
    _, _, _, _, line = await _add_lines(db_sessionmaker, job_id, [
        (few, None, {}), (many, None, {}), (many, None, {}), (many, None, {}),
        (ids["SPEAKER_00"], v[2], {}),
    ])

    await _convert(db_sessionmaker, project_id)

    assert (await _seg(db_sessionmaker, line)).speaker_id == many


@pytest.mark.asyncio
async def test_a_tie_between_characters_goes_to_the_lower_label(db_sessionmaker):
    project_id, ids, job_id = await _setup(db_sessionmaker)
    v = await _voices(db_sessionmaker)
    second = await _speaker(db_sessionmaker, project_id, "SPEAKER_05", v[2])
    first = await _speaker(db_sessionmaker, project_id, "SPEAKER_04", v[2])
    line, *_ = await _add_lines(db_sessionmaker, job_id, [(ids["SPEAKER_00"], v[2], {}), (first, None, {}),
                                                         (second, None, {})])

    await _convert(db_sessionmaker, project_id)

    assert (await _seg(db_sessionmaker, line)).speaker_id == first


@pytest.mark.asyncio
async def test_a_voice_only_the_app_picked_becomes_a_new_character(db_sessionmaker):
    """"Choose for me" picks a built-in voice unused in the episode, so the same voice repeats on unrelated
    characters: a line the owner cast by hand never moves onto (and is never confirmed on) an app-picked one."""
    project_id, ids, job_id = await _setup(db_sessionmaker)
    v = await _voices(db_sessionmaker)
    async with db_sessionmaker() as s:
        picked_by_app = Speaker(project_id=project_id, label="SPEAKER_02", voice_id=v[2], voice_auto=True)
        s.add(picked_by_app)
        await s.commit()
        auto_id = picked_by_app.id
    _, line = await _add_lines(db_sessionmaker, job_id, [(auto_id, None, {}), (ids["SPEAKER_00"], v[2], {})])

    assert await _convert(db_sessionmaker, project_id) == 1

    moved = await _seg(db_sessionmaker, line)
    assert moved.speaker_id not in (auto_id, ids["SPEAKER_00"]) and moved.who_confirmed
    async with db_sessionmaker() as s:
        new = await s.get(Speaker, moved.speaker_id)
        assert (new.voice_id, bool(new.voice_auto), new.display_name) == (v[2], False, "")


@pytest.mark.asyncio
async def test_a_voice_no_character_has_becomes_a_new_character(db_sessionmaker):
    project_id, ids, job_id = await _setup(db_sessionmaker)
    v = await _voices(db_sessionmaker)
    a, b, c = await _add_lines(db_sessionmaker, job_id, [
        (ids["SPEAKER_00"], v[2], {}), (ids["SPEAKER_01"], v[2], {}), (ids["SPEAKER_00"], v[1], {})])

    assert await _convert(db_sessionmaker, project_id) == 3

    async with db_sessionmaker() as s:
        speakers = (await s.execute(select(Speaker).where(Speaker.project_id == project_id))).scalars().all()
    new = [sp for sp in speakers if sp.id not in ids.values()]
    assert sorted(sp.voice_id for sp in new) == sorted([v[1], v[2]])   # one character per voice, shared by its lines
    labels = [sp.label for sp in speakers]
    assert len(set(labels)) == len(labels) and all(sp.label.startswith("SPEAKER_") for sp in new)
    assert {sp.display_name for sp in new} == {""}
    by_voice = {sp.voice_id: sp.id for sp in new}
    for segment_id, expected in ((a, v[2]), (b, v[2]), (c, v[1])):
        seg = await _seg(db_sessionmaker, segment_id)
        assert (seg.speaker_id, seg.voice_id, seg.who_confirmed) == (by_voice[expected], None, True)


@pytest.mark.asyncio
async def test_a_line_with_no_speaker_but_a_voice_becomes_that_characters(db_sessionmaker):
    project_id, ids, job_id = await _setup(db_sessionmaker)
    v = await _voices(db_sessionmaker)
    async with db_sessionmaker() as s:
        (await s.get(Speaker, ids["SPEAKER_01"])).voice_id = v[0]
        await s.commit()
    orphan, bare = await _add_lines(db_sessionmaker, job_id, [(None, v[0], {}), (ids["SPEAKER_00"], None, {})])

    assert await _convert(db_sessionmaker, project_id) == 1

    moved = await _seg(db_sessionmaker, orphan)
    assert (moved.speaker_id, moved.voice_id, moved.who_confirmed) == (ids["SPEAKER_01"], None, True)
    assert (await _seg(db_sessionmaker, bare)).who_confirmed is False   # no override: left alone


@pytest.mark.asyncio
async def test_nothing_else_changes(db_sessionmaker):
    project_id, ids, job_id = await _setup(db_sessionmaker)
    v = await _voices(db_sessionmaker)
    (line,) = await _add_lines(db_sessionmaker, job_id, [(ids["SPEAKER_00"], v[2], {
        "tts_audio_path": "tts/a.wav", "volume_db": -3.0, "fit_rate": 1.2, "voice_text": "km0"})])
    before = await _seg(db_sessionmaker, line)
    keep = {k: getattr(before, k) for k in (
        "source_text", "khmer_text", "english_text", "start_time", "end_time", "source_start", "source_end",
        "tts_audio_path", "volume_db", "fit_rate", "voice_text", "lane_index", "is_approved")}

    await _convert(db_sessionmaker, project_id)

    after = await _seg(db_sessionmaker, line)
    assert {k: getattr(after, k) for k in keep} == keep
    async with db_sessionmaker() as s:
        lin = await s.get(Speaker, ids["SPEAKER_00"])
        assert (lin.label, lin.display_name, lin.voice_id) == ("SPEAKER_00", "Lin Dong", None)


@pytest.mark.asyncio
async def test_upgrade_runs_once_and_resumes_prints(monkeypatch, uploads, background_db):
    db = background_db
    project_id, ids, job_id = await _setup(db)
    v = await _voices(db)
    _vocals(uploads, project_id, job_id)
    done, missing_a, missing_b = await _add_lines(db, job_id, [
        (ids["SPEAKER_00"], v[2], {}), (ids["SPEAKER_00"], None, {}), (ids["SPEAKER_01"], None, {})])
    async with db() as s:
        s.add(LinePrint(segment_id=done, job_id=job_id, voice_print=voice(7).tobytes(), speaking_score=None,
                        model_version=store.MODEL_VERSION))
        await s.commit()
    calls = []
    _prints(monkeypatch, [LinePrints(voice(3), None, None, None), LinePrints(voice(4), None, None, None)], calls)

    await upgrade.upgrade_projects(db)

    async with db() as s:
        assert (await s.get(Project, project_id)).characters_version == upgrade.CHARACTERS_VERSION
    assert calls == [[(3.0, 5.0), (6.0, 8.0)]]   # only the two lines without a row
    assert set(await _line_prints(db, job_id)) == {done, missing_a, missing_b}
    assert (await _seg(db, done)).voice_id is None   # converted (new character: nobody had that voice)

    # a later start-up: nothing moves again, no print is made again
    again, = await _add_lines(db, job_id, [(ids["SPEAKER_00"], v[1], {})])
    async with db() as s:
        s.add(LinePrint(segment_id=again, job_id=job_id, model_version=store.MODEL_VERSION))
        await s.commit()
    await upgrade.upgrade_projects(db)
    assert calls == [[(3.0, 5.0), (6.0, 8.0)]]
    assert (await _seg(db, again)).voice_id == v[1]


@pytest.mark.asyncio
async def test_a_project_made_with_characters_is_never_converted(client, monkeypatch, uploads, background_db):
    """A project created from 0.5.0 on has characters from its first episode: a line's own voice there is casting
    only (a flashback), so the next start-up's conversion must leave it on its character, and never listens to its
    voices."""
    db = background_db
    project_id = (await client.post("/api/v1/projects/", json={"name": "New series"})).json()["id"]
    assert await _version(db, project_id) == upgrade.CHARACTERS_VERSION
    job_id = await _episode(db, project_id, status=JobStatus.COMPLETED)
    v, clips = await _voices_with_clips(db, uploads)
    lin = await _speaker(db, project_id, "SPEAKER_00", v[0], "Lin Dong")
    flashback, plain = await _voiced(db, uploads, project_id, job_id, [(lin, v[1], {}), (lin, None, {})])
    calls = []
    _hear(monkeypatch, {**clips, str(uploads / project_id / job_id / "tts" / "seg_1.wav"): _heard((2, 1.0))}, calls)

    await upgrade.upgrade_projects(db)

    seg = await _seg(db, flashback)
    assert (seg.speaker_id, seg.voice_id, seg.who_confirmed) == (lin, v[1], False)
    assert ((await _seg(db, plain)).speaker_id, calls) == (lin, [])
    assert len(await _project_speakers(db, project_id)) == 1


@pytest.mark.asyncio
async def test_prints_backfill_recomputes_stale_rows(monkeypatch, uploads, background_db):
    db = background_db
    project_id, ids, job_id = await _setup(db)
    _vocals(uploads, project_id, job_id)
    stale, fresh = await _add_lines(db, job_id, [(ids["SPEAKER_00"], None, {}), (ids["SPEAKER_00"], None, {})])
    async with db() as s:
        s.add_all([LinePrint(segment_id=stale, job_id=job_id, voice_print=voice(1).tobytes(), model_version="old"),
                   LinePrint(segment_id=fresh, job_id=job_id, voice_print=voice(2).tobytes(),
                             model_version=store.MODEL_VERSION)])
        await s.commit()
    calls = []
    _prints(monkeypatch, [LinePrints(voice(9), None, None, None)], calls)

    await upgrade.upgrade_projects(db)

    assert calls == [[(0.0, 2.0)]]
    rows = await _line_prints(db, job_id)
    assert rows[stale].model_version == store.MODEL_VERSION and rows[stale].voice_print == voice(9).tobytes()
    assert rows[fresh].voice_print == voice(2).tobytes()


@pytest.mark.asyncio
async def test_models_that_cannot_run_never_stop_the_start_up(monkeypatch, uploads, background_db):
    db = background_db
    project_id, ids, job_id = await _setup(db)
    _vocals(uploads, project_id, job_id)
    await _add_lines(db, job_id, [(ids["SPEAKER_00"], None, {})])
    _prints(monkeypatch, ImportError("no onnxruntime"))

    await upgrade.upgrade_projects(db)   # no exception

    assert await _line_prints(db, job_id) == {}


async def _episode_needing_prints(db, uploads, project_id, created_at, at: float) -> str:
    """An episode made at `created_at` (with its vocals) whose one line, at `at` s, has no prints yet."""
    async with db() as s:
        job = Job(project_id=project_id, status=JobStatus.COMPLETED, video_path="v.mp4", created_at=created_at)
        s.add(job)
        await s.flush()
        s.add(Segment(job_id=job.id, start_time=at, end_time=at + 1.0, source_start=at, source_end=at + 1.0,
                      source_text="句", khmer_text="km"))
        await s.commit()
        job_id = job.id
    _vocals(uploads, project_id, job_id)
    return job_id


async def _new_project(db, name) -> str:
    async with db() as s:
        project = Project(name=name)
        s.add(project)
        await s.commit()
        return project.id


@pytest.mark.asyncio
async def test_the_backfill_starts_with_the_project_worked_on_most_recently(monkeypatch, uploads, background_db):
    """The project the owner is working on gets complete profiles first: projects by their newest episode, newest
    first, and each project's episodes newest first."""
    db = background_db
    older, newer = await _new_project(db, "Older series"), await _new_project(db, "Newer series")
    await _episode_needing_prints(db, uploads, older, datetime(2026, 10, 10), at=10.0)
    await _episode_needing_prints(db, uploads, newer, datetime(2026, 10, 1), at=1.0)
    await _episode_needing_prints(db, uploads, newer, datetime(2026, 10, 20), at=20.0)
    calls = []
    _prints(monkeypatch, [LinePrints(voice(1), None, None, None)], calls)

    assert await upgrade.backfill_prints(db) is True

    assert calls == [[(20.0, 21.0)], [(1.0, 2.0)], [(10.0, 11.0)]]


@pytest.mark.asyncio
async def test_an_episode_whose_prints_fail_does_not_stop_the_others(monkeypatch, uploads, background_db, caplog):
    """One bad episode (a damaged vocals file, say) is logged and skipped: the others still get their prints."""
    db = background_db
    project_id = await _new_project(db, "Series")
    bad = await _episode_needing_prints(db, uploads, project_id, datetime(2026, 10, 20), at=20.0)
    good = await _episode_needing_prints(db, uploads, project_id, datetime(2026, 10, 1), at=1.0)
    calls = []

    def fake(video_path, vocals_path, spans, with_faces=True):
        calls.append(list(spans))
        if spans == [(20.0, 21.0)]:
            raise RuntimeError("ffmpeg could not read vocals.wav")
        return [LinePrints(voice(1), None, None, None)]
    monkeypatch.setattr(prints_module, "prints_for_spans", fake)

    with caplog.at_level(logging.WARNING):
        assert await upgrade.backfill_prints(db) is True

    assert calls == [[(20.0, 21.0)], [(1.0, 2.0)]]
    assert await _line_prints(db, bad) == {} and len(await _line_prints(db, good)) == 1
    assert any("ffmpeg could not read vocals.wav" in r.getMessage() for r in caplog.records)
    assert store.backfill_pending() == []   # saved or failed, neither holds its project back any more


@pytest.mark.asyncio
async def test_queued_jobs_wait_until_their_prints_are_saved_or_fail(monkeypatch, uploads, background_db):
    """Ruling AL: the start-up queues the jobs the backfill will print (those with vocals: the others never get
    prints); a queued job's confirmed lines keep its project's lines from being sure until the job leaves the
    queue — its prints saved, or their making failed (a permanently failing episode holds nothing back)."""
    db = background_db
    project_id = await _new_project(db, "Series")
    saved = await _episode_needing_prints(db, uploads, project_id, datetime(2026, 10, 20), at=20.0)
    failing = await _episode_needing_prints(db, uploads, project_id, datetime(2026, 10, 1), at=1.0)
    voiceless = await _episode_needing_prints(db, uploads, project_id, datetime(2026, 10, 10), at=10.0)
    (uploads / project_id / voiceless / "vocals.wav").unlink()
    async with db() as s:
        lin = Speaker(project_id=project_id, label="SPEAKER_00", voice_id=None)
        s.add(lin)
        await s.flush()
        for seg in (await s.execute(select(Segment))).scalars():
            seg.speaker_id, seg.who_confirmed = lin.id, True
        await s.commit()

    assert await upgrade.queue_backfill(db) == 2
    assert store.backfill_pending() == [saved, failing]
    async with db() as s:
        known = await store.known_characters(s, project_id)
    assert (known.waiting, known.complete) == (2, False)

    seen = []

    def fake(video_path, vocals_path, spans, with_faces=True):
        seen.append(list(store.backfill_pending()))   # a job stays queued while its prints are being made
        if spans == [(1.0, 2.0)]:
            raise RuntimeError("ffmpeg could not read vocals.wav")
        return [LinePrints(voice(0), None, None, None)]
    monkeypatch.setattr(prints_module, "prints_for_spans", fake)
    await upgrade.backfill_prints(db)

    assert seen == [[saved, failing], [failing]]
    assert store.backfill_pending() == []
    async with db() as s:
        known = await store.known_characters(s, project_id)
    assert (known.waiting, known.complete, list(known.profiles)) == (0, True, [lin.id])


class _NoImport:
    """A meta path finder that refuses (and notes) every import of the models' libraries."""
    def __init__(self, names):
        self.names, self.tried = names, []

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in self.names:
            self.tried.append(name)
            raise ImportError(f"{name} was imported")
        return None


@pytest.mark.asyncio
async def test_below_macos_13_4_the_backfill_never_imports_the_models(monkeypatch, uploads, background_db, caplog):
    """onnxruntime's wheels need macOS 13.4: on an older Mac the start-up backfill stops as "can't run here"
    before anything imports onnxruntime or sherpa_onnx (it runs at every launch)."""
    db = background_db
    project_id = await _new_project(db, "Series")
    job_id = await _episode_needing_prints(db, uploads, project_id, datetime(2026, 10, 20), at=0.0)
    vocals = uploads / project_id / job_id / "vocals.wav"   # real sound, so the models would really be reached
    subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=220:d=2",
                    str(vocals)], check=True)
    guard = _NoImport({"onnxruntime", "sherpa_onnx"})
    for name in [n for n in sys.modules if n.split(".")[0] in guard.names]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "meta_path", [guard, *sys.meta_path])
    from app.services.characters import faces, lips, voiceprint
    for loader in (faces._session, lips._session, voiceprint._extractor):
        loader.cache_clear()     # nothing loaded before: a run would have to import
    monkeypatch.setattr(platform, "mac_ver", lambda: ("13.3.1", ("", "", ""), "arm64"))

    with caplog.at_level(logging.WARNING):
        assert await upgrade.backfill_prints(db) is False

    assert guard.tried == []
    assert await _line_prints(db, job_id) == {}
    assert any("can't run here" in r.getMessage() and "13.4" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("release, ok", [
    ("13.3.1", False), ("12.7.6", False), ("13", False), ("10.16", False),
    ("13.4", True), ("13.4.1", True), ("14", True), ("26.6.2", True), ("", True),   # "": not a Mac (CI on Linux)
])
def test_the_models_need_macos_13_4(monkeypatch, release, ok):
    from app.services.characters import models
    monkeypatch.setattr(platform, "mac_ver", lambda: (release, ("", "", ""), "arm64"))
    assert models.runtime_supported() is ok


async def _crop(folder, name, content=b"jpeg") -> str:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(content)
    return str(path)


@pytest.mark.asyncio
async def test_refresh_faces_picks_the_best_confirmed_crop_that_exists(tmp_path, db_sessionmaker):
    project_id, ids = await _project(db_sessionmaker)
    job_id = await _episode(db_sessionmaker, project_id, status=JobStatus.COMPLETED)
    lin = ids["SPEAKER_00"]
    low = await _crop(tmp_path, "low.jpg")
    unconfirmed_better = await _crop(tmp_path, "open.jpg")
    best_confirmed_but_gone = str(tmp_path / "gone.jpg")
    async with db_sessionmaker() as s:
        rows = [(0.4, low, True), (0.99, unconfirmed_better, False), (0.95, best_confirmed_but_gone, True)]
        for k, (score, path, confirmed) in enumerate(rows):
            seg = Segment(job_id=job_id, speaker_id=lin, start_time=10 + k, end_time=11 + k, who_confirmed=confirmed)
            s.add(seg)
            await s.flush()
            s.add(LinePrint(segment_id=seg.id, job_id=job_id, speaking_score=score, face_crop_path=path,
                            model_version=store.MODEL_VERSION))
        await s.commit()

    async with db_sessionmaker() as s:
        await upgrade.refresh_faces(s, project_id)

    async with db_sessionmaker() as s:
        assert (await s.get(Speaker, lin)).face_crop_path == low
        assert (await s.get(Speaker, ids["SPEAKER_01"])).face_crop_path is None   # no crop at all


@pytest.mark.asyncio
async def test_the_sweep_removes_only_unreferenced_crops(uploads, background_db):
    db = background_db
    project_id, ids, job_id = await _setup(db)
    (line,) = await _add_lines(db, job_id, [(ids["SPEAKER_00"], None, {})])
    faces = uploads / project_id / job_id / "faces"
    kept = await _crop(faces, f"{line}.jpg")
    orphan = await _crop(faces, "deleted-line.jpg")
    async with db() as s:
        s.add(LinePrint(segment_id=line, job_id=job_id, face_crop_path=kept, model_version=store.MODEL_VERSION))
        await s.commit()

    await upgrade.sweep_orphan_crops(db)

    assert os.path.exists(kept) and not os.path.exists(orphan)


@pytest.mark.asyncio
async def test_unmerge_restores_the_thumbnail(client, tmp_path, db_sessionmaker):
    project_id, ids = await _project(db_sessionmaker)
    job_id = await _episode(db_sessionmaker, project_id, status=JobStatus.COMPLETED)
    crops = {}
    async with db_sessionmaker() as s:
        for label, score in (("SPEAKER_00", 0.5), ("SPEAKER_01", 0.9)):
            seg = (await s.execute(select(Segment).where(Segment.speaker_id == ids[label]))).scalars().one()
            crops[label] = await _crop(tmp_path, f"{label}.jpg")
            row = await s.get(LinePrint, seg.id)
            row.face_crop_path, row.speaking_score = crops[label], score
        await s.commit()
    async with db_sessionmaker() as s:
        await upgrade.refresh_faces(s, project_id)

    async def thumbs():
        async with db_sessionmaker() as s:
            return {sp.label: sp.face_crop_path for sp in
                    (await s.execute(select(Speaker).where(Speaker.project_id == project_id))).scalars()}

    assert await thumbs() == crops
    body = (await client.post(f"/api/v1/speakers/{ids['SPEAKER_01']}/merge", json={"into": ids["SPEAKER_00"]})).json()
    assert await thumbs() == {"SPEAKER_00": crops["SPEAKER_01"]}   # the merged character's best face
    resp = await client.post(f"/api/v1/speakers/{ids['SPEAKER_00']}/unmerge",
                             json={"removed": body["removed"], "moved": body["moved"], "relinked": body["relinked"]})
    assert resp.status_code == 200
    assert await thumbs() == crops


# ── Ruling AN: each old line goes to the character of the voice it was made with ────────────────────────────────
# Before 0.5.0 the owner re-used a label for another actor by changing its voice, so a label's voice today doesn't
# say who spoke its older lines; the voice the line's Khmer audio was made with does (one voice = one actor). The
# voice prints are replaced where the upgrade looks them up (voiceprint.file_print): reference clip of voice k = unit
# vector k; a line's audio = mostly the voice it was made with.

def _heard(*weights: tuple[int, float]) -> np.ndarray:
    """A voice print close to voices k (weight w each), normalised."""
    v = np.zeros(192, np.float32)
    for k, w in weights:
        v[k] = w
    return v / np.linalg.norm(v)


def _hear(monkeypatch, prints: dict, calls: list | None = None):
    """voiceprint.file_print answers from `prints` {file path: vector or exception}."""
    from app.services.characters import voiceprint

    def fake(path):
        if calls is not None:
            calls.append(str(path))
        found = prints[str(path)]
        if isinstance(found, Exception):
            raise found
        return found
    monkeypatch.setattr(voiceprint, "file_print", fake)


async def _voices_with_clips(db, uploads, n=4) -> tuple[list[str], dict]:
    """n voices with a reference clip each (voice k's print: unit vector k). Returns (ids, {clip path: print})."""
    folder = uploads / "voices"
    folder.mkdir(parents=True, exist_ok=True)
    async with db() as s:
        rows = []
        for k in range(n):
            clip = folder / f"ref{k}.flac"
            clip.write_bytes(b"fLaC")
            rows.append(Voice(name=f"Voice {k}", mode="ultimate", reference_audio_path=str(clip)))
        s.add_all(rows)
        await s.commit()
        return [r.id for r in rows], {r.reference_audio_path: voice(k) for k, r in enumerate(rows)}


async def _voiced(db, uploads, project_id, job_id, lines) -> list[str]:
    """lines: (speaker id, own voice id or None, extra fields); each gets a voice file tts/seg_<k>.wav unless
    "tts_audio_path" is given. Returns the ids."""
    tts = uploads / project_id / job_id / "tts"
    tts.mkdir(parents=True, exist_ok=True)
    rows = []
    for k, (speaker_id, voice_id, fields) in enumerate(lines):
        if "tts_audio_path" not in fields:
            (tts / f"seg_{k}.wav").write_bytes(b"RIFF")
            fields = {**fields, "tts_audio_path": str(tts / f"seg_{k}.wav")}
        rows.append((speaker_id, voice_id, fields))
    return await _add_lines(db, job_id, rows)


async def _speaker_row(db, speaker_id) -> Speaker:
    async with db() as s:
        return await s.get(Speaker, speaker_id)


async def _project_speakers(db, project_id) -> list[Speaker]:
    async with db() as s:
        return (await s.execute(select(Speaker).where(Speaker.project_id == project_id))).scalars().all()


async def _version(db, project_id) -> int | None:
    async with db() as s:
        return (await s.get(Project, project_id)).characters_version


async def _cast_voices(db, casting: dict) -> None:
    """casting: {speaker id: voice id} chosen by a person."""
    async with db() as s:
        for speaker_id, voice_id in casting.items():
            sp = await s.get(Speaker, speaker_id)
            sp.voice_id, sp.voice_auto = voice_id, False
        await s.commit()


def test_the_upgrade_is_version_2_and_new_projects_start_there():
    assert upgrade.CHARACTERS_VERSION == 2 and upgrade.VOICE_MARGIN == 0.05
    assert Project.__table__.c.characters_version.default.arg == upgrade.CHARACTERS_VERSION


@pytest.mark.asyncio
async def test_a_label_reused_for_another_actor_gives_each_line_the_character_of_its_voice(
        monkeypatch, uploads, background_db):
    """SPEAKER_00's voice is A today, but the owner re-used the label for another actor by changing its voice: its
    lines made with B go to B's character, those made with A stay."""
    db = background_db
    project_id, ids, job_id = await _setup(db)
    v, clips = await _voices_with_clips(db, uploads)
    lin, mei = ids["SPEAKER_00"], ids["SPEAKER_01"]
    await _cast_voices(db, {lin: v[0], mei: v[1]})
    made_with_a, made_with_b = await _voiced(db, uploads, project_id, job_id, [(lin, None, {}), (lin, None, {})])
    tts = uploads / project_id / job_id / "tts"
    _hear(monkeypatch, {**clips, str(tts / "seg_0.wav"): _heard((0, 1.0), (3, 0.3)),
                        str(tts / "seg_1.wav"): _heard((1, 1.0), (2, 0.3))})

    assert await upgrade.convert_project(db, project_id) == 1

    stays, moved = await _seg(db, made_with_a), await _seg(db, made_with_b)
    assert (stays.speaker_id, stays.who_confirmed, stays.who_check) == (lin, False, None)
    assert (moved.speaker_id, moved.voice_id, moved.who_confirmed, moved.who_check) == (mei, None, True, None)
    assert await _version(db, project_id) == 2


@pytest.mark.asyncio
async def test_a_voice_no_character_has_gets_one_new_character_for_all_its_lines(monkeypatch, uploads, background_db):
    """The actor voiced with Yim Thearith's voice in ep01 died there: no speaker has that voice today."""
    db = background_db
    project_id, ids, job_id = await _setup(db)
    v, clips = await _voices_with_clips(db, uploads)
    lin, mei = ids["SPEAKER_00"], ids["SPEAKER_01"]
    await _cast_voices(db, {lin: v[0], mei: v[1]})
    first, second = await _voiced(db, uploads, project_id, job_id, [(lin, None, {}), (mei, None, {})])
    tts = uploads / project_id / job_id / "tts"
    _hear(monkeypatch, {**clips, str(tts / "seg_0.wav"): _heard((3, 1.0)), str(tts / "seg_1.wav"): _heard((3, 1.0))})

    assert await upgrade.convert_project(db, project_id) == 2

    new = [sp for sp in await _project_speakers(db, project_id) if sp.id not in ids.values()]
    assert [(sp.label, sp.voice_id, bool(sp.voice_auto), sp.display_name) for sp in new] == [
        ("SPEAKER_02", v[3], False, "")]
    for line in (first, second):
        seg = await _seg(db, line)
        assert (seg.speaker_id, seg.voice_id, seg.who_confirmed) == (new[0].id, None, True)


@pytest.mark.asyncio
async def test_a_line_whose_voice_is_not_clear_stays(monkeypatch, uploads, background_db):
    db = background_db
    project_id, ids, job_id = await _setup(db)
    v, clips = await _voices_with_clips(db, uploads)
    lin, mei = ids["SPEAKER_00"], ids["SPEAKER_01"]
    await _cast_voices(db, {lin: v[0], mei: v[1]})
    close, clear = await _voiced(db, uploads, project_id, job_id, [(lin, None, {}), (lin, None, {})])
    tts = uploads / project_id / job_id / "tts"
    # Mei's voice by 0.04 and by 0.06 over Lin Dong's
    _hear(monkeypatch, {**clips, str(tts / "seg_0.wav"): _heard((1, 0.74), (0, 0.70), (2, 0.2)),
                        str(tts / "seg_1.wav"): _heard((1, 0.76), (0, 0.70), (2, 0.2))})

    assert await upgrade.convert_project(db, project_id) == 1

    assert ((await _seg(db, close)).speaker_id, (await _seg(db, close)).who_confirmed) == (lin, False)
    assert (await _seg(db, clear)).speaker_id == mei


@pytest.mark.asyncio
async def test_a_hand_set_voice_wins_over_what_its_audio_sounds_like(monkeypatch, uploads, background_db):
    """A line with its own voice moves to the character cast with that voice, as in v1; its audio isn't even heard."""
    db = background_db
    project_id, ids, job_id = await _setup(db)
    v, clips = await _voices_with_clips(db, uploads)
    lin, mei = ids["SPEAKER_00"], ids["SPEAKER_01"]
    await _cast_voices(db, {lin: v[0], mei: v[1]})
    (line,) = await _voiced(db, uploads, project_id, job_id, [(lin, v[1], {})])
    calls = []
    _hear(monkeypatch, {**clips, str(uploads / project_id / job_id / "tts" / "seg_0.wav"): _heard((2, 1.0))}, calls)

    assert await upgrade.convert_project(db, project_id) == 1

    seg = await _seg(db, line)
    assert (seg.speaker_id, seg.voice_id, seg.who_confirmed) == (mei, None, True)
    assert not any("seg_0" in c for c in calls)


@pytest.mark.asyncio
async def test_a_line_with_no_voice_or_a_missing_voice_file_stays(monkeypatch, uploads, background_db):
    db = background_db
    project_id, ids, job_id = await _setup(db)
    v, clips = await _voices_with_clips(db, uploads)
    lin, mei = ids["SPEAKER_00"], ids["SPEAKER_01"]
    await _cast_voices(db, {lin: v[0], mei: v[1]})
    gone = str(uploads / project_id / job_id / "tts" / "seg_gone.wav")
    unvoiced, missing = await _voiced(db, uploads, project_id, job_id, [
        (lin, None, {"tts_audio_path": ""}), (lin, None, {"tts_audio_path": gone})])
    calls = []
    _hear(monkeypatch, {**clips, gone: _heard((1, 1.0))}, calls)

    assert await upgrade.convert_project(db, project_id) == 0

    for line in (unvoiced, missing):
        seg = await _seg(db, line)
        assert (seg.speaker_id, seg.who_confirmed) == (lin, False)
    assert gone not in calls
    assert await _version(db, project_id) == 2


@pytest.mark.asyncio
async def test_a_line_whose_audio_cannot_be_read_is_skipped(monkeypatch, uploads, background_db):
    db = background_db
    project_id, ids, job_id = await _setup(db)
    v, clips = await _voices_with_clips(db, uploads)
    lin, mei = ids["SPEAKER_00"], ids["SPEAKER_01"]
    await _cast_voices(db, {lin: v[0], mei: v[1]})
    broken, fine = await _voiced(db, uploads, project_id, job_id, [(lin, None, {}), (lin, None, {})])
    tts = uploads / project_id / job_id / "tts"
    _hear(monkeypatch, {**clips, str(tts / "seg_0.wav"): RuntimeError("ffmpeg could not read seg_0.wav"),
                        str(tts / "seg_1.wav"): _heard((1, 1.0))})

    assert await upgrade.convert_project(db, project_id) == 1

    assert (await _seg(db, broken)).speaker_id == lin and (await _seg(db, fine)).speaker_id == mei


@pytest.mark.asyncio
async def test_nothing_but_the_character_changes_on_a_moved_line(monkeypatch, uploads, background_db):
    db = background_db
    project_id, ids, job_id = await _setup(db)
    v, clips = await _voices_with_clips(db, uploads)
    lin, mei = ids["SPEAKER_00"], ids["SPEAKER_01"]
    await _cast_voices(db, {lin: v[0], mei: v[1]})
    (line,) = await _voiced(db, uploads, project_id, job_id, [(lin, None, {
        "volume_db": -3.0, "fit_rate": 1.2, "voice_text": "km0", "voice_offset": 0.25, "fade_in_secs": 0.1,
        "fade_out_secs": 0.2, "tts_duration_secs": 1.75, "voice_speed": 1.1, "voice_filter": "radio",
        "level_gain_db": 2.5, "is_approved": True, "notes": "n", "lane_index": 2, "khmer_machine": "km0"})])
    _hear(monkeypatch, {**clips, str(uploads / project_id / job_id / "tts" / "seg_0.wav"): _heard((1, 1.0))})
    columns = [c.name for c in Segment.__table__.columns if c.name not in ("speaker_id", "who_confirmed", "who_check")]
    before = await _seg(db, line)
    keep = {c: getattr(before, c) for c in columns}

    assert await upgrade.convert_project(db, project_id) == 1

    after = await _seg(db, line)
    assert after.speaker_id == mei and {c: getattr(after, c) for c in columns} == keep
    speakers = {sp.id: (sp.label, sp.display_name, sp.voice_id) for sp in await _project_speakers(db, project_id)}
    assert speakers == {lin: ("SPEAKER_00", "Lin Dong", v[0]), mei: ("SPEAKER_01", "Mei", v[1])}


@pytest.mark.asyncio
async def test_a_label_left_without_lines_stays(monkeypatch, uploads, background_db):
    db = background_db
    project_id, ids, job_id = await _setup(db)
    v, clips = await _voices_with_clips(db, uploads)
    lin, mei = ids["SPEAKER_00"], ids["SPEAKER_01"]
    await _cast_voices(db, {lin: v[0], mei: v[1]})
    async with db() as s:   # _project's first-episode lines: Mei's moves to Lin Dong, Lin Dong's is unvoiced
        for seg in (await s.execute(select(Segment).where(Segment.speaker_id == mei))).scalars():
            seg.tts_audio_path = str(uploads / "mei.wav")
        await s.commit()
    (uploads / "mei.wav").write_bytes(b"RIFF")
    _hear(monkeypatch, {**clips, str(uploads / "mei.wav"): _heard((0, 1.0))})
    await upgrade.queue_backfill(db)   # the start-up: the project's lines as they are now

    assert await upgrade.convert_project(db, project_id) == 1

    assert {sp.id for sp in await _project_speakers(db, project_id)} == {lin, mei}


@pytest.mark.asyncio
async def test_the_character_a_line_moves_to_is_one_a_person_cast_with_the_most_lines(monkeypatch, uploads,
                                                                                    background_db):
    db = background_db
    project_id, ids, job_id = await _setup(db)
    v, clips = await _voices_with_clips(db, uploads)
    lin = ids["SPEAKER_00"]
    await _cast_voices(db, {lin: v[0]})
    async with db() as s:
        app_picked = Speaker(project_id=project_id, label="SPEAKER_02", voice_id=v[2], voice_auto=True)
        few = Speaker(project_id=project_id, label="SPEAKER_03", voice_id=v[2], voice_auto=False)
        many = Speaker(project_id=project_id, label="SPEAKER_04", voice_id=v[2], voice_auto=None)
        s.add_all([app_picked, few, many])
        await s.commit()
        app_picked, few, many = app_picked.id, few.id, many.id
    line, *_ = await _voiced(db, uploads, project_id, job_id, [
        (lin, None, {}), (app_picked, None, {"tts_audio_path": ""}), (app_picked, None, {"tts_audio_path": ""}),
        (app_picked, None, {"tts_audio_path": ""}), (few, None, {"tts_audio_path": ""}),
        (many, None, {"tts_audio_path": ""}), (many, None, {"tts_audio_path": ""})])
    _hear(monkeypatch, {**clips, str(uploads / project_id / job_id / "tts" / "seg_0.wav"): _heard((2, 1.0))})

    assert await upgrade.convert_project(db, project_id) == 1

    assert (await _seg(db, line)).speaker_id == many


def _while_heard(monkeypatch, heard: dict, trigger: str, change):
    """voiceprint.file_print answers from `heard`; hearing the file ending in `trigger` first runs `change` (an async
    function: the user, in the editor, while the audio is heard in the worker thread)."""
    from app.services.characters import voiceprint
    loop = asyncio.get_running_loop()

    def fake(path):
        if path.endswith(trigger):
            asyncio.run_coroutine_threadsafe(change(), loop).result(timeout=10)
        return heard[path]
    monkeypatch.setattr(voiceprint, "file_print", fake)


@pytest.mark.parametrize("change", ["picked", "speaker", "own voice", "voice file path", "voice made again",
                                    "character re-cast"])
@pytest.mark.asyncio
async def test_a_line_changed_while_the_voices_are_heard_is_left_alone(change, monkeypatch, uploads, background_db):
    """The audio is heard with no session open (minutes on a big project). A line the user changes meanwhile — its
    character picked or changed, a voice of its own (casting only since 0.5.0), its voice made again, or its
    character re-cast — is left as the user left it: what was heard no longer says anything about it."""
    db = background_db
    project_id, ids, job_id = await _setup(db)
    v, clips = await _voices_with_clips(db, uploads)
    lin, mei = ids["SPEAKER_00"], ids["SPEAKER_01"]
    uncle = await _speaker(db, project_id, "SPEAKER_02", v[2], "Uncle Wang")
    await _cast_voices(db, {lin: v[0], mei: v[1]})
    (line,) = await _voiced(db, uploads, project_id, job_id, [(lin, None, {})])
    voice_file = uploads / project_id / job_id / "tts" / "seg_0.wav"
    other_file = uploads / project_id / job_id / "tts" / "other.wav"
    other_file.write_bytes(b"RIFF")
    expected = {"speaker_id": lin, "voice_id": None, "who_confirmed": False}

    async def user():
        async with db() as s:
            seg = await s.get(Segment, line)
            if change == "picked":
                seg.who_confirmed = True
                expected["who_confirmed"] = True
            elif change == "speaker":
                seg.speaker_id = uncle
                expected["speaker_id"] = uncle
            elif change == "own voice":
                seg.voice_id = v[3]
                expected["voice_id"] = v[3]
            elif change == "voice file path":
                seg.tts_audio_path = str(other_file)
            elif change == "voice made again":
                voice_file.write_bytes(b"RIFF again")
                made = os.stat(voice_file).st_mtime_ns
                os.utime(voice_file, ns=(made + 5_000_000_000, made + 5_000_000_000))
            elif change == "character re-cast":
                (await s.get(Speaker, lin)).voice_id = v[2]
            await s.commit()
    _while_heard(monkeypatch, {**clips, str(voice_file): _heard((1, 1.0))}, "seg_0.wav", user)

    assert await upgrade.convert_project(db, project_id) == 0   # Mei's voice, were it still as it was heard

    seg = await _seg(db, line)
    assert {k: getattr(seg, k) for k in expected} == expected
    assert len(await _project_speakers(db, project_id)) == 3


@pytest.mark.asyncio
async def test_a_line_with_its_own_voice_picked_while_the_voices_are_heard_is_left_alone(monkeypatch, uploads,
                                                                                       background_db):
    db = background_db
    project_id, ids, job_id = await _setup(db)
    v, clips = await _voices_with_clips(db, uploads)
    lin, mei = ids["SPEAKER_00"], ids["SPEAKER_01"]
    await _cast_voices(db, {lin: v[0], mei: v[1]})
    own, _plain = await _voiced(db, uploads, project_id, job_id, [(lin, v[1], {}), (lin, None, {})])

    async def user_picks_lin_dong():
        async with db() as s:
            seg = await s.get(Segment, own)
            seg.who_confirmed, seg.who_check = True, None
            await s.commit()
    tts = uploads / project_id / job_id / "tts"
    _while_heard(monkeypatch, {**clips, str(tts / "seg_1.wav"): _heard((0, 1.0))}, "seg_1.wav", user_picks_lin_dong)

    await upgrade.convert_project(db, project_id)

    seg = await _seg(db, own)
    assert (seg.speaker_id, seg.voice_id, seg.who_confirmed) == (lin, v[1], True)


@pytest.mark.asyncio
async def test_a_pick_made_after_the_start_up_before_the_conversion_is_kept(monkeypatch, uploads, background_db):
    """The project's turn may come long after the start-up (its episodes' prints come one project after another:
    33 minutes on the owner's data). What the user did meanwhile in 0.5.0 stands."""
    db = background_db
    project_id, ids, job_id = await _setup(db)
    v, clips = await _voices_with_clips(db, uploads)
    lin, mei = ids["SPEAKER_00"], ids["SPEAKER_01"]
    await _cast_voices(db, {lin: v[0], mei: v[1]})
    picked, unchanged, voiced_again = await _voiced(db, uploads, project_id, job_id, [
        (mei, None, {}), (lin, None, {}), (lin, None, {})])
    tts = uploads / project_id / job_id / "tts"
    await upgrade.queue_backfill(db)          # the start-up
    async with db() as s:                     # the user, in 0.5.0
        seg = await s.get(Segment, picked)    # says this line is Lin Dong
        seg.speaker_id, seg.who_confirmed, seg.who_check = lin, True, None
        await s.commit()
    (tts / "seg_2.wav").write_bytes(b"RIFF again")
    made = os.stat(tts / "seg_2.wav").st_mtime_ns
    os.utime(tts / "seg_2.wav", ns=(made + 5_000_000_000, made + 5_000_000_000))   # makes this one's voice again
    async with db() as s:
        flashback = Segment(job_id=job_id, speaker_id=lin, voice_id=v[3], start_time=30.0, end_time=31.0,
                            source_text="新", khmer_text="km", tts_audio_path=str(tts / "seg_2.wav"))
        s.add(flashback)                      # and adds a line, with a voice of its own for a flashback
        await s.commit()
        flashback = flashback.id
    _hear(monkeypatch, {**clips, str(tts / "seg_0.wav"): _heard((1, 1.0)), str(tts / "seg_1.wav"): _heard((3, 1.0)),
                        str(tts / "seg_2.wav"): _heard((1, 1.0))})

    assert await upgrade.convert_project(db, project_id) == 1   # the background reaches the project

    assert (await _seg(db, picked)).speaker_id == lin
    (new,) = [sp for sp in await _project_speakers(db, project_id) if sp.id not in (lin, mei)]
    seg = await _seg(db, unchanged)           # untouched since the start-up: goes to its voice's new character
    assert (new.voice_id, seg.speaker_id, seg.who_confirmed) == (v[3], new.id, True)
    assert (await _seg(db, voiced_again)).speaker_id == lin
    seg = await _seg(db, flashback)
    assert (seg.speaker_id, seg.voice_id, seg.who_confirmed) == (lin, v[3], False)


@pytest.mark.asyncio
async def test_a_character_re_cast_after_the_start_up_keeps_its_lines(monkeypatch, uploads, background_db):
    db = background_db
    project_id, ids, job_id = await _setup(db)
    v, clips = await _voices_with_clips(db, uploads)
    lin, mei = ids["SPEAKER_00"], ids["SPEAKER_01"]
    await _cast_voices(db, {lin: v[0], mei: v[1]})
    (line,) = await _voiced(db, uploads, project_id, job_id, [(lin, None, {})])
    await upgrade.queue_backfill(db)
    await _cast_voices(db, {lin: v[2]})       # the user re-casts Lin Dong in 0.5.0
    _hear(monkeypatch, {**clips, str(uploads / project_id / job_id / "tts" / "seg_0.wav"): _heard((0, 1.0))})

    assert await upgrade.convert_project(db, project_id) == 0

    assert (await _seg(db, line)).speaker_id == lin
    assert len(await _project_speakers(db, project_id)) == 2


@pytest.mark.asyncio
async def test_the_engines_own_voice_is_heard_not_the_clip_with_its_effects(monkeypatch, uploads, background_db):
    """A line's clip carries its filter (echo, phone…), speed and level, which can fool a voice print; the engine's
    voice before them (seg_<id>_raw.wav) is heard when it's there."""
    db = background_db
    project_id, ids, job_id = await _setup(db)
    v, clips = await _voices_with_clips(db, uploads)
    lin, mei = ids["SPEAKER_00"], ids["SPEAKER_01"]
    await _cast_voices(db, {lin: v[0], mei: v[1]})
    with_raw, without_raw = await _voiced(db, uploads, project_id, job_id, [(lin, None, {}), (lin, None, {})])
    tts = uploads / project_id / job_id / "tts"
    raw = tts / f"seg_{with_raw}_raw.wav"
    raw.write_bytes(b"RIFF")
    calls = []
    _hear(monkeypatch, {**clips, str(raw): _heard((1, 1.0)), str(tts / "seg_0.wav"): _heard((0, 1.0)),
                        str(tts / "seg_1.wav"): _heard((1, 1.0))}, calls)

    assert await upgrade.convert_project(db, project_id) == 2

    assert str(raw) in calls and str(tts / "seg_0.wav") not in calls and str(tts / "seg_1.wav") in calls
    assert (await _seg(db, with_raw)).speaker_id == mei and (await _seg(db, without_raw)).speaker_id == mei


@pytest.mark.asyncio
async def test_with_no_voice_to_compare_with_only_hand_set_voices_move(monkeypatch, uploads, background_db, caplog):
    db = background_db
    project_id, ids, job_id = await _setup(db)
    v = await _voices(db)   # voices with no reference clip
    lin, mei = ids["SPEAKER_00"], ids["SPEAKER_01"]
    await _cast_voices(db, {lin: v[0], mei: v[1]})
    hand_set, plain = await _voiced(db, uploads, project_id, job_id, [(lin, v[1], {}), (lin, None, {})])
    calls = []
    _hear(monkeypatch, {}, calls)

    with caplog.at_level(logging.WARNING):
        assert await upgrade.convert_project(db, project_id) == 1

    assert ((await _seg(db, hand_set)).speaker_id, (await _seg(db, plain)).speaker_id, calls) == (mei, lin, [])
    said = [r for r in caplog.records if r.name == upgrade.__name__ and r.levelno >= logging.WARNING]
    assert len(said) == 1 and "only lines with their own voice" in said[0].getMessage()
    assert await _version(db, project_id) == 2


@pytest.mark.parametrize("cannot", [
    ImportError("No module named 'sherpa_onnx'"),
    "models.ModelMissing",
    "old macOS",
    RuntimeError("the voice model crashed"),
])
@pytest.mark.asyncio
async def test_when_the_voices_cannot_be_heard_only_hand_set_voices_move(cannot, monkeypatch, uploads, background_db,
                                                                       caplog):
    """The models can't run here (or failed): v1's conversion only, the project still marked done (never retried
    at every start-up), one log line."""
    from app.services.characters import models
    db = background_db
    project_id, ids, job_id = await _setup(db)
    v, clips = await _voices_with_clips(db, uploads)
    lin, mei = ids["SPEAKER_00"], ids["SPEAKER_01"]
    await _cast_voices(db, {lin: v[0], mei: v[1]})
    hand_set, heard = await _voiced(db, uploads, project_id, job_id, [(lin, v[1], {}), (lin, None, {})])
    calls = []
    if cannot == "old macOS":   # stops before anything could import the models' libraries
        monkeypatch.setattr(platform, "mac_ver", lambda: ("13.3.1", ("", "", ""), "arm64"))
        _hear(monkeypatch, {}, calls)
    else:
        error = models.ModelMissing("eres2netv2.onnx is missing") if cannot == "models.ModelMissing" else cannot
        _hear(monkeypatch, {path: error for path in clips}, calls)

    with caplog.at_level(logging.WARNING):
        assert await upgrade.convert_project(db, project_id) == 1

    assert ((await _seg(db, hand_set)).speaker_id, (await _seg(db, heard)).speaker_id) == (mei, lin)
    assert (calls == []) is (cannot == "old macOS")
    assert await _version(db, project_id) == 2
    said = [r for r in caplog.records if r.name == upgrade.__name__ and r.levelno >= logging.WARNING]
    assert len(said) == 1 and "only lines with their own voice" in said[0].getMessage()


@pytest.mark.asyncio
async def test_a_project_upgraded_by_version_1_is_converted_again_once(monkeypatch, uploads, background_db):
    db = background_db
    project_id, ids, job_id = await _setup(db)
    async with db() as s:
        (await s.get(Project, project_id)).characters_version = 1   # the owner's test copy of 0.5.0
        await s.commit()
    v, clips = await _voices_with_clips(db, uploads)
    lin, mei = ids["SPEAKER_00"], ids["SPEAKER_01"]
    await _cast_voices(db, {lin: v[0], mei: v[1]})
    (line,) = await _voiced(db, uploads, project_id, job_id, [(lin, None, {})])
    calls = []
    _hear(monkeypatch, {**clips, str(uploads / project_id / job_id / "tts" / "seg_0.wav"): _heard((1, 1.0))}, calls)

    await upgrade.upgrade_projects(db)

    assert (await _seg(db, line)).speaker_id == mei and await _version(db, project_id) == 2
    heard_once = list(calls)
    await upgrade.upgrade_projects(db)   # the next start-up: done
    assert calls == heard_once


@pytest.mark.asyncio
async def test_the_rules_move_nothing_a_second_time(monkeypatch, uploads, background_db):
    """A start-up that quits mid-conversion redoes the project at the next one: a line already on the character of
    its voice doesn't move."""
    db = background_db
    project_id, ids, job_id = await _setup(db)
    v, clips = await _voices_with_clips(db, uploads)
    lin, mei = ids["SPEAKER_00"], ids["SPEAKER_01"]
    await _cast_voices(db, {lin: v[0], mei: v[1]})
    await _voiced(db, uploads, project_id, job_id, [(lin, None, {}), (lin, v[3], {})])
    tts = uploads / project_id / job_id / "tts"
    _hear(monkeypatch, {**clips, str(tts / "seg_0.wav"): _heard((1, 1.0)), str(tts / "seg_1.wav"): _heard((3, 1.0))})
    assert await upgrade.convert_project(db, project_id) == 2
    speakers = len(await _project_speakers(db, project_id))
    async with db() as s:
        (await s.get(Project, project_id)).characters_version = None
        await s.commit()

    assert await upgrade.convert_project(db, project_id) == 0
    assert len(await _project_speakers(db, project_id)) == speakers


@pytest.mark.asyncio
async def test_while_a_project_waits_for_its_conversion_no_line_of_it_is_sure(monkeypatch, uploads, background_db):
    """Its characters may still hold several actors: the start-up queues the project's episodes, awaited, before the
    background starts, so an analysis or Detect speakers can't mark lines sure from them — even when every line
    already has today's prints (the owner's test copy at version 1)."""
    db = background_db
    project_id, ids, job_id = await _setup(db)
    async with db() as s:   # _project's first episode: its confirmed lines have today's prints
        first = (await s.execute(select(Job.id).where(Job.project_id == project_id, Job.id != job_id))).scalar_one()
    _vocals(uploads, project_id, first)
    other_project = await _new_project(db, "Another series")   # made with characters: nothing to convert

    await upgrade.queue_backfill(db)

    assert set(store.backfill_pending()) == {first, job_id}
    assert store.conversions_pending() == [project_id]
    async with db() as s:
        known = await store.known_characters(s, project_id)
        assert (known.complete, known.first_episode) == (False, False)
        assert (await store.known_characters(s, other_project)).complete

    _hear(monkeypatch, {})
    await upgrade.finish_upgrade(db)

    assert store.backfill_pending() == [] and store.conversions_pending() == []
    async with db() as s:
        assert (await store.known_characters(s, project_id)).complete
    assert await _version(db, project_id) == 2


@pytest.mark.asyncio
async def test_a_project_is_converted_before_its_prints_are_made(monkeypatch, uploads, background_db):
    db = background_db
    project_id, ids, job_id = await _setup(db)
    _vocals(uploads, project_id, job_id)
    v, clips = await _voices_with_clips(db, uploads)
    lin, mei = ids["SPEAKER_00"], ids["SPEAKER_01"]
    await _cast_voices(db, {lin: v[0], mei: v[1]})
    (line,) = await _voiced(db, uploads, project_id, job_id, [(lin, None, {})])
    order = []

    def file_print(path):
        order.append("voices")
        return {**clips, str(uploads / project_id / job_id / "tts" / "seg_0.wav"): _heard((1, 1.0))}[path]
    from app.services.characters import voiceprint
    monkeypatch.setattr(voiceprint, "file_print", file_print)

    def prints(video_path, vocals_path, spans, with_faces=True):
        order.append(("prints", [k for k in store.conversions_pending()]))
        return [LinePrints(voice(5), None, None, None) for _ in spans]
    monkeypatch.setattr(prints_module, "prints_for_spans", prints)

    await upgrade.upgrade_projects(db)

    assert order[-1] == ("prints", []) and set(order[:-1]) == {"voices"}
    assert (await _seg(db, line)).speaker_id == mei
