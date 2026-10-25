"""Same character? suggestions: two labels that look like one character, offered for the user's click. Nothing is
merged here; the Speakers card's Same person as… does the work."""
import json

import numpy as np
import pytest
from sqlalchemy import select

from app.models.models import Job, JobStatus, LinePrint, Project, Segment, Speaker, Voice
from app.services.characters import store, suggestions
from app.tests.test_characters_pipeline import face, voice  # noqa: F401


async def _voice(db_sessionmaker) -> str:
    async with db_sessionmaker() as s:
        v = Voice(name="Kim Sreymom", mode="design")
        s.add(v)
        await s.commit()
        return v.id


async def _cast(db_sessionmaker, specs) -> tuple:
    """specs: [(label, voice_id, voice_auto, voice print or None, face print or None)]; each character gets one
    confirmed line with those prints. Returns (project id, {label: speaker id})."""
    async with db_sessionmaker() as s:
        project = Project(name="Series", source_lang="zh", target_lang="km")
        s.add(project)
        await s.flush()
        job = Job(project_id=project.id, status=JobStatus.COMPLETED, progress=100, video_path="ep.mp4")
        s.add(job)
        await s.flush()
        ids = {}
        for k, (label, voice_id, auto, vp, fp) in enumerate(specs):
            sp = Speaker(project_id=project.id, label=label, voice_id=voice_id, voice_auto=auto)
            s.add(sp)
            await s.flush()
            ids[label] = sp.id
            line = Segment(job_id=job.id, speaker_id=sp.id, start_time=k * 3.0, end_time=k * 3.0 + 2.0,
                           who_confirmed=True)
            s.add(line)
            await s.flush()
            s.add(LinePrint(segment_id=line.id, job_id=job.id, speaking_score=0.9, model_version=store.MODEL_VERSION,
                            voice_print=None if vp is None else vp.tobytes(),
                            face_print=None if fp is None else fp.tobytes()))
        await s.commit()
        return project.id, ids


def _near(k: int, other: int, weight: float, size: int) -> np.ndarray:
    v = np.zeros(size, np.float32)
    v[k] = weight
    v[other] = np.sqrt(1 - weight ** 2)
    return v


async def _get(client, project_id):
    r = await client.get(f"/api/v1/projects/{project_id}/character-suggestions")
    assert r.status_code == 200
    return r.json()


@pytest.mark.asyncio
async def test_two_characters_cast_with_one_voice_are_suggested(client, db_sessionmaker):
    kim = await _voice(db_sessionmaker)
    project_id, ids = await _cast(db_sessionmaker, [
        ("SPEAKER_01", kim, False, voice(0), None), ("SPEAKER_03", kim, False, voice(5), None),
        ("SPEAKER_04", None, False, voice(9), None)])
    got = await _get(client, project_id)
    a, b = sorted([ids["SPEAKER_01"], ids["SPEAKER_03"]])
    assert got == [{"a": a, "b": b, "reason": "same voice"}]


@pytest.mark.asyncio
async def test_profiles_that_match_in_voice_and_face_are_suggested(client, db_sessionmaker):
    assert (suggestions.SAME_VOICE, suggestions.SAME_FACE) == (0.75, 0.6)
    project_id, ids = await _cast(db_sessionmaker, [
        ("SPEAKER_00", None, False, voice(0), face(0)),
        ("SPEAKER_01", None, False, _near(0, 1, 0.9, 192), _near(0, 1, 0.8, 128)),
        ("SPEAKER_02", None, False, _near(0, 2, 0.9, 192), face(7))])   # voice alone is not enough
    got = await _get(client, project_id)
    a, b = sorted([ids["SPEAKER_00"], ids["SPEAKER_01"]])
    assert got == [{"a": a, "b": b, "reason": "same voice and face"}]


@pytest.mark.asyncio
async def test_a_dismissed_pair_never_comes_back(client, db_sessionmaker):
    kim = await _voice(db_sessionmaker)
    project_id, ids = await _cast(db_sessionmaker, [
        ("SPEAKER_01", kim, False, voice(0), None), ("SPEAKER_03", kim, False, voice(5), None)])
    one = (await _get(client, project_id))[0]
    r = await client.post(f"/api/v1/projects/{project_id}/character-suggestions/dismiss", json={"a": one["a"], "b": one["b"]})
    assert r.status_code == 204
    assert await _get(client, project_id) == []
    assert await _get(client, project_id) == []


@pytest.mark.asyncio
async def test_nothing_is_merged_by_itself(client, db_sessionmaker):
    kim = await _voice(db_sessionmaker)
    project_id, ids = await _cast(db_sessionmaker, [
        ("SPEAKER_01", kim, False, voice(0), None), ("SPEAKER_03", kim, False, voice(5), None)])
    assert await _get(client, project_id)
    async with db_sessionmaker() as s:
        rows = (await s.execute(select(Speaker).where(Speaker.project_id == project_id))).scalars().all()
        lines = (await s.execute(select(Segment))).scalars().all()
        project = await s.get(Project, project_id)
    assert {r.id for r in rows} == set(ids.values())
    assert {line.speaker_id for line in lines} == set(ids.values())
    assert project.character_dismissals is None


@pytest.mark.asyncio
async def test_auto_picked_voices_are_not_suggested(client, db_sessionmaker):
    kim = await _voice(db_sessionmaker)
    project_id, _ = await _cast(db_sessionmaker, [
        ("SPEAKER_01", kim, True, voice(0), None), ("SPEAKER_03", kim, True, voice(5), None),
        ("SPEAKER_04", kim, False, voice(9), None)])
    assert await _get(client, project_id) == []


@pytest.mark.asyncio
async def test_a_dismissal_in_either_order_is_one_pair(client, db_sessionmaker):
    kim = await _voice(db_sessionmaker)
    project_id, ids = await _cast(db_sessionmaker, [
        ("SPEAKER_01", kim, False, voice(0), None), ("SPEAKER_03", kim, False, voice(5), None)])
    a, b = ids["SPEAKER_01"], ids["SPEAKER_03"]
    url = f"/api/v1/projects/{project_id}/character-suggestions/dismiss"
    assert (await client.post(url, json={"a": b, "b": a})).status_code == 204
    assert (await client.post(url, json={"a": a, "b": b})).status_code == 204
    async with db_sessionmaker() as s:
        project = await s.get(Project, project_id)
    assert json.loads(project.character_dismissals) == [sorted([a, b])]
    assert await _get(client, project_id) == []


@pytest.mark.asyncio
async def test_dismissals_are_checked(client, db_sessionmaker):
    kim = await _voice(db_sessionmaker)
    project_id, ids = await _cast(db_sessionmaker, [("SPEAKER_01", kim, False, None, None)])
    other, other_ids = await _cast(db_sessionmaker, [("SPEAKER_01", kim, False, None, None)])
    url = f"/api/v1/projects/{project_id}/character-suggestions/dismiss"
    a = ids["SPEAKER_01"]
    assert (await client.post(url, json={"a": a, "b": a})).status_code == 400
    assert (await client.post(url, json={"a": a, "b": other_ids["SPEAKER_01"]})).status_code == 400
    assert (await client.post(url, json={"a": a, "b": "nope"})).status_code == 400
    assert (await client.post("/api/v1/projects/nope/character-suggestions/dismiss",
                              json={"a": a, "b": a})).status_code == 404
    assert (await client.get("/api/v1/projects/nope/character-suggestions")).status_code == 404


@pytest.mark.asyncio
async def test_a_character_without_a_face_profile_is_not_suggested_by_face(client, db_sessionmaker):
    project_id, _ = await _cast(db_sessionmaker, [
        ("SPEAKER_00", None, False, voice(0), face(0)),
        ("SPEAKER_01", None, False, _near(0, 1, 0.9, 192), None)])
    assert await _get(client, project_id) == []


@pytest.mark.asyncio
async def test_suggestions_are_ordered_same_voice_first_then_voice_cosine(client, db_sessionmaker):
    kim = await _voice(db_sessionmaker)
    project_id, ids = await _cast(db_sessionmaker, [
        ("SPEAKER_00", None, False, voice(0), face(0)),
        ("SPEAKER_01", None, False, _near(0, 1, 0.8, 192), _near(0, 1, 0.9, 128)),
        ("SPEAKER_02", None, False, _near(0, 2, 0.95, 192), _near(0, 2, 0.9, 128)),
        ("SPEAKER_05", kim, False, voice(20), None), ("SPEAKER_06", kim, False, voice(21), None)])
    got = await _get(client, project_id)
    assert got[0]["reason"] == "same voice"
    rest = [(g["a"], g["b"]) for g in got[1:]]
    assert all(g["reason"] == "same voice and face" for g in got[1:])
    p0, p2 = ids["SPEAKER_00"], ids["SPEAKER_02"]
    assert rest[0] == tuple(sorted([p0, p2]))
