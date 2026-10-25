"""
Existing projects become characters (0.5.0). Before characters, the owner fixed a line whose voice sounded like
another actor by changing the line's own Khmer voice; the upgrade turns those voices into characters, once per
project, and computes the prints of every older line in the background so profiles exist when it ends.

The models are replaced at the service boundary (characters.prints.prints_for_spans), as in test_characters_pipeline.
"""
import os

import pytest
from sqlalchemy import select

from app.models.models import Job, JobStatus, LinePrint, Project, Segment, Speaker, Voice
from app.services.characters import store, upgrade
from app.services.characters.types import LinePrints
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
    """A project with Lin Dong (SPEAKER_00) and Mei (SPEAKER_01), and one episode (no lines yet)."""
    project_id, ids = await _project(db_sessionmaker)
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
        assert (await s.get(Project, project_id)).characters_version == 1
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
