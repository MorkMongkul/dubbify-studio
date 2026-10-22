"""
Merging two speakers. A character the speaker detection didn't recognise in a
later episode gets a second label (SPEAKER_05 for the same person as
SPEAKER_03); the user used to move every line by hand. Merging moves all of
one speaker's lines, in every episode, to the other and removes the duplicate;
the Story notes' character follows. It can be undone: the removed speaker is
created again and the moved lines go back to it.
"""
import pytest
from sqlalchemy import select

from app.models.models import Job, JobStatus, Project, Segment, Speaker, Voice
from app.services import story_notes


async def _series(db_sessionmaker):
    async with db_sessionmaker() as s:
        project = Project(name="Series", story_notes=story_notes.apply_user_edit(
            None, {"character": {"zh": "谢三", "km": "សៀ សាន", "about": "", "speaker": "SPEAKER_05"}}))
        other = Project(name="Other")
        s.add_all([project, other])
        await s.flush()
        voice = Voice(name="Hero", mode="design")
        s.add(voice)
        await s.flush()
        keep = Speaker(project_id=project.id, label="SPEAKER_03", display_name="谢三", voice_id=voice.id)
        dup = Speaker(project_id=project.id, label="SPEAKER_05", display_name="SPEAKER_05")
        stranger = Speaker(project_id=other.id, label="SPEAKER_00", display_name="SPEAKER_00")
        s.add_all([keep, dup, stranger])
        await s.flush()
        ep1 = Job(project_id=project.id, status=JobStatus.COMPLETED, progress=100, video_path="1.mp4")
        ep2 = Job(project_id=project.id, status=JobStatus.COMPLETED, progress=100, video_path="2.mp4")
        s.add_all([ep1, ep2])
        await s.flush()
        lines = [
            Segment(job_id=ep1.id, start_time=0, end_time=1, source_text="走", speaker_id=keep.id),
            Segment(job_id=ep1.id, start_time=1, end_time=2, source_text="好", speaker_id=dup.id, tts_audio_path="x.wav"),
            Segment(job_id=ep2.id, start_time=0, end_time=1, source_text="嗯", speaker_id=dup.id),
        ]
        s.add_all(lines)
        await s.commit()
        return {"project": project.id, "keep": keep.id, "dup": dup.id, "stranger": stranger.id,
                "lines": [l.id for l in lines], "voice": voice.id}


async def _speaker_of(db_sessionmaker, segment_ids):
    async with db_sessionmaker() as s:
        rows = (await s.execute(select(Segment.id, Segment.speaker_id).where(Segment.id.in_(segment_ids)))).all()
        return {seg_id: speaker_id for seg_id, speaker_id in rows}


@pytest.mark.asyncio
async def test_merging_moves_every_line_in_every_episode_and_removes_the_duplicate(client, db_sessionmaker):
    ids = await _series(db_sessionmaker)
    resp = await client.post(f"/api/v1/speakers/{ids['dup']}/merge", json={"into": ids["keep"]})
    assert resp.status_code == 200
    body = resp.json()
    assert sorted(body["moved"]) == sorted(ids["lines"][1:])
    assert body["removed"]["label"] == "SPEAKER_05"
    assert set((await _speaker_of(db_sessionmaker, ids["lines"])).values()) == {ids["keep"]}
    async with db_sessionmaker() as s:
        assert await s.get(Speaker, ids["dup"]) is None
        line = await s.get(Segment, ids["lines"][1])
        assert line.tts_audio_path == "x.wav"            # voiced lines keep their audio until voiced again


@pytest.mark.asyncio
async def test_the_story_character_follows_the_merge(client, db_sessionmaker):
    ids = await _series(db_sessionmaker)
    await client.post(f"/api/v1/speakers/{ids['dup']}/merge", json={"into": ids["keep"]})
    async with db_sessionmaker() as s:
        notes = (await s.get(Project, ids["project"])).story_notes
    assert [c["speaker"] for c in notes["characters"]] == ["SPEAKER_03"]


@pytest.mark.asyncio
async def test_a_merge_can_be_undone(client, db_sessionmaker):
    ids = await _series(db_sessionmaker)
    body = (await client.post(f"/api/v1/speakers/{ids['dup']}/merge", json={"into": ids["keep"]})).json()
    removed = body["removed"]
    back = (await client.post(f"/api/v1/projects/{ids['project']}/speakers", json={
        "label": removed["label"], "display_name": removed["display_name"], "voice_id": removed["voice_id"]})).json()
    resp = await client.post(f"/api/v1/speakers/{ids['keep']}/merge",
                             json={"into": back["id"], "segment_ids": body["moved"]})
    assert resp.status_code == 200 and resp.json()["removed"] is None    # moving some lines removes nobody
    speakers = await _speaker_of(db_sessionmaker, ids["lines"])
    assert speakers[ids["lines"][0]] == ids["keep"]
    assert speakers[ids["lines"][1]] == speakers[ids["lines"][2]] == back["id"]


@pytest.mark.asyncio
async def test_a_merge_needs_two_speakers_of_one_project(client, db_sessionmaker):
    ids = await _series(db_sessionmaker)
    assert (await client.post(f"/api/v1/speakers/{ids['dup']}/merge", json={"into": ids["dup"]})).status_code == 400
    assert (await client.post(f"/api/v1/speakers/{ids['dup']}/merge", json={"into": ids["stranger"]})).status_code == 400
    assert (await client.post(f"/api/v1/speakers/{ids['dup']}/merge", json={"into": "nobody"})).status_code == 404
    resp = await client.post(f"/api/v1/speakers/{ids['dup']}/merge",
                             json={"into": ids["keep"], "segment_ids": [ids["lines"][0]]})   # not one of its lines
    assert resp.status_code == 400
    assert set((await _speaker_of(db_sessionmaker, ids["lines"][1:])).values()) == {ids["dup"]}
