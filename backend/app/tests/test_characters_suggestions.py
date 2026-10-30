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
    assert got == [{"a": a, "b": b, "reason": "same voice", "a_lines": 1, "b_lines": 1, "a_sample": None, "b_sample": None}]


@pytest.mark.asyncio
async def test_a_suggestion_carries_each_characters_lines_in_the_whole_project(client, db_sessionmaker):
    kim = await _voice(db_sessionmaker)
    project_id, ids = await _cast(db_sessionmaker, [
        ("SPEAKER_01", kim, False, voice(0), None), ("SPEAKER_03", kim, False, voice(5), None)])
    async with db_sessionmaker() as s:   # three more lines for SPEAKER_03, two of them in another episode
        first = (await s.execute(select(Job).where(Job.project_id == project_id))).scalars().one()
        other = Job(project_id=project_id, status=JobStatus.COMPLETED, progress=100, video_path="ep2.mp4")
        s.add(other)
        await s.flush()
        for job_id, k in [(first.id, 30), (other.id, 0), (other.id, 3)]:
            s.add(Segment(job_id=job_id, speaker_id=ids["SPEAKER_03"], start_time=k, end_time=k + 2.0))
        await s.commit()
    (got,) = await _get(client, project_id)
    counts = {got["a"]: got["a_lines"], got["b"]: got["b_lines"]}
    assert counts == {ids["SPEAKER_01"]: 1, ids["SPEAKER_03"]: 4}


@pytest.mark.asyncio
async def test_profiles_that_match_in_voice_and_face_are_suggested(client, db_sessionmaker):
    assert (suggestions.SAME_VOICE, suggestions.SAME_FACE) == (0.75, 0.75)
    project_id, ids = await _cast(db_sessionmaker, [
        ("SPEAKER_00", None, False, voice(0), face(0)),
        ("SPEAKER_01", None, False, _near(0, 1, 0.9, 192), _near(0, 1, 0.8, 128)),
        ("SPEAKER_02", None, False, _near(0, 2, 0.9, 192), face(7))])   # voice alone is not enough
    got = await _get(client, project_id)
    a, b = sorted([ids["SPEAKER_00"], ids["SPEAKER_01"]])
    assert got == [{"a": a, "b": b, "reason": "same voice and face", "a_lines": 1, "b_lines": 1, "a_sample": None, "b_sample": None}]


@pytest.mark.parametrize("face_cosine, suggested", [(0.74, False), (0.76, True)])
def test_a_face_as_close_as_two_actors_on_one_label_is_not_enough(face_cosine, suggested):
    # On the owner's project, labels holding two actors came within 0.74 in face of another character
    from app.services.characters.decide import Profile
    speakers = [Speaker(id="a", label="SPEAKER_00"), Speaker(id="b", label="SPEAKER_01")]
    profiles = {"a": Profile(voice(0), face(0), 5),
                "b": Profile(_near(0, 1, 0.9, 192), _near(0, 1, face_cosine, 128), 5)}
    got = suggestions.find(speakers, profiles, set())
    assert [g["reason"] for g in got] == (["same voice and face"] if suggested else [])


@pytest.mark.parametrize("auto_a, auto_b, suggested", [
    (False, False, False),   # both chosen by a person, and different: two actors (one voice = one actor)
    (True, False, True),     # "Choose for me" picked one: it says nothing about the actor
    (True, True, True),
])
def test_two_characters_a_person_cast_with_different_voices_are_never_the_same(auto_a, auto_b, suggested):
    """Ruling AO: the owner casts one voice per actor for the whole series, so different voices a person chose mean
    different people — however close their voice and face profiles are."""
    from app.services.characters.decide import Profile
    speakers = [Speaker(id="a", label="SPEAKER_00", voice_id="kim", voice_auto=auto_a),
                Speaker(id="b", label="SPEAKER_01", voice_id="sokunthea", voice_auto=auto_b)]
    profiles = {"a": Profile(voice(0), face(0), 5),
                "b": Profile(_near(0, 1, 0.95, 192), _near(0, 1, 0.95, 128), 5)}
    got = suggestions.find(speakers, profiles, set())
    assert [g["reason"] for g in got] == (["same voice and face"] if suggested else [])


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


async def _second_episode(db_sessionmaker, project_id, files=("vocals.wav",)) -> str:
    """Another episode of the project, with its separated vocals on disk (any bytes: only their place matters)."""
    from pathlib import Path
    from app.core.config import settings
    async with db_sessionmaker() as s:
        job = Job(project_id=project_id, status=JobStatus.COMPLETED, progress=100, video_path="ep2.mp4")
        s.add(job)
        await s.commit()
    folder = Path(settings.UPLOAD_DIR) / project_id / job.id
    folder.mkdir(parents=True, exist_ok=True)
    for name in files:
        (folder / name).write_bytes(b"RIFF")
    return job.id


async def _line(db_sessionmaker, job_id, speaker_id, clip, actor, score):
    async with db_sessionmaker() as s:
        line = Segment(job_id=job_id, speaker_id=speaker_id, start_time=clip[0], end_time=clip[1],
                       source_start=actor[0], source_end=actor[1], who_confirmed=True)
        s.add(line)
        await s.flush()
        s.add(LinePrint(segment_id=line.id, job_id=job_id, speaking_score=score, model_version=store.MODEL_VERSION))
        await s.commit()


def _side(got, speaker_id):
    [pair] = got
    return pair["a_sample"] if pair["a"] == speaker_id else pair["b_sample"]


@pytest.mark.asyncio
async def test_a_suggestion_offers_a_line_of_each_character_from_any_episode(client, db_sessionmaker):
    """▶ must be able to play both characters wherever they speak, not only in the episode that's open; the line
    where their face was seen speaking it best is the one to hear, from where the actor spoke."""
    kim = await _voice(db_sessionmaker)
    project_id, ids = await _cast(db_sessionmaker, [
        ("SPEAKER_01", kim, False, voice(0), None), ("SPEAKER_03", kim, False, voice(5), None)])
    other = await _second_episode(db_sessionmaker, project_id)
    await _line(db_sessionmaker, other, ids["SPEAKER_03"], clip=(40.0, 43.0), actor=(10.0, 13.0), score=2.5)
    await _line(db_sessionmaker, other, ids["SPEAKER_03"], clip=(50.0, 55.0), actor=(20.0, 25.0), score=0.2)
    got = await _get(client, project_id)
    sample = _side(got, ids["SPEAKER_03"])
    assert sample["url"].startswith(f"/uploads/{project_id}/{other}/vocals.wav")
    assert (sample["start"], sample["end"]) == (10.0, 13.0)
    # SPEAKER_01 speaks only in an episode whose voices weren't separated: nothing to play
    assert _side(got, ids["SPEAKER_01"]) is None


@pytest.mark.asyncio
async def test_a_sample_is_at_most_six_seconds_from_the_previews_own_file(client, db_sessionmaker):
    kim = await _voice(db_sessionmaker)
    project_id, ids = await _cast(db_sessionmaker, [
        ("SPEAKER_01", kim, False, voice(0), None), ("SPEAKER_03", kim, False, voice(5), None)])
    other = await _second_episode(db_sessionmaker, project_id, files=("vocals.wav", "vocals.m4a"))
    await _line(db_sessionmaker, other, ids["SPEAKER_03"], clip=(0.0, 12.0), actor=(30.0, 42.0), score=1.0)
    sample = _side(await _get(client, project_id), ids["SPEAKER_03"])
    assert sample["url"].startswith(f"/uploads/{project_id}/{other}/vocals.m4a")
    assert (sample["start"], sample["end"]) == (30.0, 36.0)
