"""
Single-voice dub projects (0.5.7): chosen when the project is created — one Edge TTS voice for every line, no speaker
work. The kind can't change afterwards; its voice, speed and "fully automatic" switch can.
"""
import pytest

pytestmark = pytest.mark.asyncio


async def test_a_single_voice_project_has_its_settings(client):
    body = (await client.post("/api/v1/projects/", json={"name": "Quick", "dub_mode": "single_voice"})).json()
    assert body["dub_mode"] == "single_voice"
    assert body["voice_name"] == "km-KH-PisethNeural" and body["voice_rate"] == 0 and body["auto_export"] is False


async def test_a_project_is_a_character_project_unless_chosen(client):
    body = (await client.post("/api/v1/projects/", json={"name": "Drama"})).json()
    assert body["dub_mode"] == "characters"


@pytest.mark.parametrize("bad", [{"voice_name": "en-US-AriaNeural"}, {"voice_rate": 80}, {"dub_mode": "narration"}])
async def test_unknown_settings_are_refused(client, bad):
    resp = await client.post("/api/v1/projects/", json={"name": "Quick", "dub_mode": "single_voice", **bad})
    assert resp.status_code == 422


async def test_voice_speed_and_automatic_can_change_but_not_the_kind(client):
    project = (await client.post("/api/v1/projects/", json={"name": "Quick", "dub_mode": "single_voice"})).json()
    resp = await client.patch(f"/api/v1/projects/{project['id']}", json={
        "voice_name": "km-KH-SreymomNeural", "voice_rate": 20, "auto_export": True})
    body = resp.json()
    assert (body["voice_name"], body["voice_rate"], body["auto_export"]) == ("km-KH-SreymomNeural", 20, True)
    resp = await client.patch(f"/api/v1/projects/{project['id']}", json={"dub_mode": "characters"})
    assert resp.status_code == 422


async def test_a_new_voice_or_speed_marks_every_voiced_line_to_be_voiced_again(client, db_sessionmaker):
    from sqlalchemy import select
    from app.models.models import Job, JobStatus, Segment
    project = (await client.post("/api/v1/projects/", json={"name": "Quick", "dub_mode": "single_voice"})).json()
    async with db_sessionmaker() as s:
        job = Job(project_id=project["id"], status=JobStatus.COMPLETED, video_path="v.mp4")
        s.add(job)
        await s.flush()
        s.add(Segment(job_id=job.id, start_time=0, end_time=1, khmer_text="មួយ", tts_audio_path="/v/1.wav",
                      voice_text="មួយ"))
        await s.commit()
        job_id = job.id

    async def voice_texts():
        async with db_sessionmaker() as s:
            return (await s.execute(select(Segment.voice_text).where(Segment.job_id == job_id))).scalars().all()
    await client.patch(f"/api/v1/projects/{project['id']}", json={"auto_export": True})
    assert await voice_texts() == ["មួយ"]                       # not a voice change
    await client.patch(f"/api/v1/projects/{project['id']}", json={"voice_rate": 20})
    assert await voice_texts() == [None]                        # Generate voices & export makes it again
