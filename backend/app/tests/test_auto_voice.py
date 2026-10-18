"""
A voice for every speaker. A speaker nobody gave a voice used to be voiced
from a blank description with no fixed voice, so each of its lines came out as
a different random voice. Now such a speaker gets a built-in voice the first
time it is voiced (a man's, a woman's or a child's, by the pitch of the
actor's original lines, preferring one no other speaker uses) and keeps it.
"""
import asyncio
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

import app.core.database as core_db
from app.api.routes.tts import _resolve_voice
from app.core.config import settings
from app.models.models import Job, JobStatus, Project, Segment, Speaker, Voice
from app.services import auto_voice
from app.services.pipeline_common import is_confirmed_speaker

RATE = 16000


def _tone(hz: float, seconds: float) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    return (0.3 * np.sin(2 * np.pi * hz * t)).astype("float32")


BUILT_IN = [  # id, gender, age_group, roles
    ("builtin-01-man-a", "man", "20s", ["Lead"]),
    ("builtin-01-man-b", "man", "20s", ["Lead"]),
    ("builtin-02-woman-a", "woman", "20s", ["Lead"]),
    ("builtin-02-woman-b", "woman", "30s", ["Doctor"]),
    ("builtin-13-girl-a", "girl", "child", ["Child"]),
    ("builtin-23-narrator-a", "man", "40s", ["Narrator"]),
]


@pytest.fixture
def episode(monkeypatch, tmp_path, db_sessionmaker):
    """A project and episode whose vocals hold a man (0–2 s), a woman (2–4 s) and a child (4–6 s)."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))

    async def make(with_built_ins: bool = True) -> dict:
        async with db_sessionmaker() as db:
            project = Project(name="Ep")
            db.add(project)
            await db.flush()
            job = Job(project_id=project.id, status=JobStatus.COMPLETED, progress=100)
            db.add(job)
            await db.flush()
            vocals = Path(tmp_path) / project.id / job.id / "vocals.wav"
            vocals.parent.mkdir(parents=True)
            sf.write(str(vocals), np.concatenate([_tone(120, 2), _tone(220, 2), _tone(330, 2)]), RATE)
            if with_built_ins:
                for vid, gender, age, roles in BUILT_IN:
                    db.add(Voice(id=vid, name=vid, mode="ultimate", description=vid,
                                 reference_transcript="x", gender=gender, age_group=age,
                                 personality=["Warm"], roles=roles))
            await db.commit()
            return {"project": project.id, "job": job.id}

    return make


async def _speaker(db_sessionmaker, project_id: str, label: str, **fields) -> str:
    async with db_sessionmaker() as db:
        sp = Speaker(project_id=project_id, label=label, display_name=label,
                     voice_design_prompt="A natural speaking voice", **fields)
        db.add(sp)
        await db.commit()
        return sp.id


async def _line(db_sessionmaker, job_id: str, start: float, speaker_id: str | None) -> str:
    async with db_sessionmaker() as db:
        seg = Segment(job_id=job_id, speaker_id=speaker_id, start_time=start, end_time=start + 2,
                      source_text="你好", khmer_text="សួស្ដី")
        db.add(seg)
        await db.commit()
        return seg.id


async def _resolve(db_sessionmaker, segment_id: str) -> dict:
    async with db_sessionmaker() as db:
        seg = (await db.execute(select(Segment).where(Segment.id == segment_id))).scalar_one()
        return await _resolve_voice(seg, db)


async def _voice_of(db_sessionmaker, speaker_id: str) -> tuple:
    async with db_sessionmaker() as db:
        sp = await db.get(Speaker, speaker_id)
        return sp.voice_id, sp.voice_auto


def test_pitch_tells_men_women_and_children_apart():
    assert abs(auto_voice.median_pitch(_tone(120, 1), RATE) - 120) < 5
    assert abs(auto_voice.median_pitch(_tone(220, 1), RATE) - 220) < 8
    assert auto_voice.median_pitch(np.zeros(RATE, dtype="float32"), RATE) is None
    assert [auto_voice.kind_from_pitch(hz) for hz in (120, 215, 320, None)] == ["man", "woman", "child", None]


@pytest.mark.asyncio
async def test_a_speaker_without_a_voice_gets_a_fitting_built_in_voice_and_keeps_it(episode, db_sessionmaker):
    ids = await episode()
    hero = await _speaker(db_sessionmaker, ids["project"], "SPEAKER_00")
    first = await _line(db_sessionmaker, ids["job"], 0.0, hero)       # the man's stretch
    later = await _line(db_sessionmaker, ids["job"], 0.0, hero)

    kwargs = await _resolve(db_sessionmaker, first)
    assert await _voice_of(db_sessionmaker, hero) == ("builtin-01-man-a", True)
    assert kwargs["description"] == "builtin-01-man-a"
    await _resolve(db_sessionmaker, later)
    assert await _voice_of(db_sessionmaker, hero) == ("builtin-01-man-a", True)


@pytest.mark.asyncio
async def test_each_speaker_gets_a_voice_no_other_speaker_uses(episode, db_sessionmaker):
    ids = await episode()
    a = await _speaker(db_sessionmaker, ids["project"], "SPEAKER_00")
    b = await _speaker(db_sessionmaker, ids["project"], "SPEAKER_01")
    await _resolve(db_sessionmaker, await _line(db_sessionmaker, ids["job"], 0.0, a))
    await _resolve(db_sessionmaker, await _line(db_sessionmaker, ids["job"], 0.0, b))
    assert (await _voice_of(db_sessionmaker, a))[0] == "builtin-01-man-a"
    assert (await _voice_of(db_sessionmaker, b))[0] == "builtin-01-man-b"


@pytest.mark.asyncio
async def test_women_and_children_get_womens_and_childrens_voices(episode, db_sessionmaker):
    ids = await episode()
    woman = await _speaker(db_sessionmaker, ids["project"], "SPEAKER_01")
    child = await _speaker(db_sessionmaker, ids["project"], "SPEAKER_02")
    await _resolve(db_sessionmaker, await _line(db_sessionmaker, ids["job"], 2.0, woman))
    await _resolve(db_sessionmaker, await _line(db_sessionmaker, ids["job"], 4.0, child))
    assert (await _voice_of(db_sessionmaker, woman))[0] == "builtin-02-woman-a"
    assert (await _voice_of(db_sessionmaker, child))[0] == "builtin-13-girl-a"


@pytest.mark.asyncio
async def test_lines_of_one_speaker_voiced_at_once_share_one_voice(episode, db_sessionmaker):
    """Batch voicing runs several lines at a time: they must not each pick a voice."""
    ids = await episode()
    hero = await _speaker(db_sessionmaker, ids["project"], "SPEAKER_00")
    lines = [await _line(db_sessionmaker, ids["job"], 0.0, hero) for _ in range(3)]
    results = await asyncio.gather(*[_resolve(db_sessionmaker, line) for line in lines])
    assert {r["description"] for r in results} == {"builtin-01-man-a"}
    assert await _voice_of(db_sessionmaker, hero) == ("builtin-01-man-a", True)


@pytest.mark.asyncio
async def test_a_line_without_a_speaker_gets_a_fixed_voice_by_its_pitch(episode, db_sessionmaker):
    ids = await episode()
    line = await _line(db_sessionmaker, ids["job"], 2.0, None)        # the woman's stretch
    first, again = await _resolve(db_sessionmaker, line), await _resolve(db_sessionmaker, line)
    assert first["description"] == again["description"] == "builtin-02-woman-a"


@pytest.mark.asyncio
async def test_without_built_in_voices_the_speakers_description_is_used(episode, db_sessionmaker):
    ids = await episode(with_built_ins=False)
    hero = await _speaker(db_sessionmaker, ids["project"], "SPEAKER_00")
    kwargs = await _resolve(db_sessionmaker, await _line(db_sessionmaker, ids["job"], 0.0, hero))
    assert kwargs == {"voice_design": "A natural speaking voice"}
    assert await _voice_of(db_sessionmaker, hero) == (None, False)


@pytest.mark.asyncio
async def test_a_voice_chosen_by_hand_is_not_automatic(client, episode, db_sessionmaker):
    ids = await episode()
    hero = await _speaker(db_sessionmaker, ids["project"], "SPEAKER_00")
    await _resolve(db_sessionmaker, await _line(db_sessionmaker, ids["job"], 0.0, hero))
    async with db_sessionmaker() as db:
        assert not is_confirmed_speaker(await db.get(Speaker, hero)), "a picked voice isn't the user's choice"

    resp = await client.patch(f"/api/v1/speakers/{hero}", json={"voice_id": "builtin-02-woman-b"})
    assert resp.status_code == 200 and resp.json()["voice_auto"] is False
    assert await _voice_of(db_sessionmaker, hero) == ("builtin-02-woman-b", False)
    async with db_sessionmaker() as db:
        assert is_confirmed_speaker(await db.get(Speaker, hero))


@pytest.mark.asyncio
async def test_a_new_speaker_can_be_created_with_a_voice(client, episode):
    ids = await episode()
    resp = await client.post(f"/api/v1/projects/{ids['project']}/speakers",
                             json={"display_name": "Hero", "voice_id": "builtin-01-man-b"})
    assert resp.status_code == 201
    assert (resp.json()["voice_id"], resp.json()["voice_auto"]) == ("builtin-01-man-b", False)
    resp = await client.post(f"/api/v1/projects/{ids['project']}/speakers",
                             json={"display_name": "Ghost", "voice_id": "no-such-voice"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_startup_adds_voice_auto_to_existing_speakers(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/old.db")
    try:
        async with engine.begin() as conn:
            await conn.execute(text(
                "CREATE TABLE speakers (id VARCHAR PRIMARY KEY, project_id VARCHAR NOT NULL, label VARCHAR(50) NOT NULL,"
                " display_name VARCHAR(100), gender VARCHAR(7), age_group VARCHAR(6), voice_design_prompt TEXT,"
                " reference_audio_path VARCHAR, voice_id VARCHAR)"))
        await core_db.init_db(engine)
        async with engine.connect() as conn:
            columns = await conn.run_sync(lambda c: {col["name"] for col in __import__("sqlalchemy").inspect(c).get_columns("speakers")})
        assert "voice_auto" in columns
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_two_speakers_voiced_at_once_get_different_voices(episode, db_sessionmaker):
    ids = await episode()
    a = await _speaker(db_sessionmaker, ids["project"], "SPEAKER_00")
    b = await _speaker(db_sessionmaker, ids["project"], "SPEAKER_01")
    lines = [await _line(db_sessionmaker, ids["job"], 0.0, a), await _line(db_sessionmaker, ids["job"], 0.0, b)]
    await asyncio.gather(*[_resolve(db_sessionmaker, line) for line in lines])
    assert {(await _voice_of(db_sessionmaker, a))[0], (await _voice_of(db_sessionmaker, b))[0]} == {
        "builtin-01-man-a", "builtin-01-man-b"}
