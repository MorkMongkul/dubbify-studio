"""
A voice for every speaker. A speaker nobody gave a voice used to be voiced
from a blank description with no fixed voice, so each of its lines came out as
a different random voice. Now such a speaker gets a built-in voice the first
time it is voiced (a man's, a woman's or a child's, by the pitch of the
actor's original lines, preferring one nobody else in the episode uses) and
keeps it.
"""
import asyncio
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.database as core_db
from app.api.routes import tts as tts_routes
from app.api.routes.tts import _resolve_voice
from app.core.config import settings
from app.core.database import Base
from app.models.models import Job, JobStatus, Project, Segment, Speaker, Voice
from app.services import auto_voice
from app.services.pipeline_common import is_confirmed_speaker
from app.services.source_separator import UNAVAILABLE_MARKER

RATE = 16000


def _tone(hz: float, seconds: float) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    return (0.3 * np.sin(2 * np.pi * hz * t)).astype("float32")


BUILT_IN = [  # id, gender, age_group, roles — ids follow the real "<nn>-<character>-<take>" pattern
    ("builtin-01-man-a", "man", "20s", ["Lead"]),
    ("builtin-01-man-b", "man", "20s", ["Lead"]),
    ("builtin-03-ceo-a", "man", "20s", ["Hero"]),
    ("builtin-02-woman-a", "woman", "20s", ["Lead"]),
    ("builtin-02-woman-b", "woman", "30s", ["Doctor"]),
    ("builtin-26-girl-7-a", "girl", "child", ["Child"]),
    ("builtin-23-narrator-a", "man", "40s", ["Narrator"]),
]


@pytest.fixture
def episode(monkeypatch, tmp_path, db_sessionmaker):
    """A project and an episode whose vocals hold a man (0–2 s), a woman (2–4 s) and a child (4–6 s)."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))

    async def make(voices=BUILT_IN, project_id: str | None = None, vocals: bool = True,
                   separation_failed: bool = False, sessions=None) -> dict:
        sessions = sessions or db_sessionmaker
        async with sessions() as db:
            if project_id is None:
                project = Project(name="Ep")
                db.add(project)
                await db.flush()
                project_id = project.id
                for vid, gender, age, roles in voices:
                    db.add(Voice(id=vid, name=vid, mode="ultimate", description=vid, reference_transcript="x",
                                 gender=gender, age_group=age, personality=["Warm"], roles=roles))
            job = Job(project_id=project_id, status=JobStatus.COMPLETED, progress=100)
            db.add(job)
            await db.flush()
            folder = Path(tmp_path) / project_id / job.id
            folder.mkdir(parents=True)
            if vocals:
                sf.write(str(folder / "vocals.wav"), np.concatenate([_tone(120, 2), _tone(220, 2), _tone(330, 2)]), RATE)
            if separation_failed:
                (folder / UNAVAILABLE_MARKER).write_text("the separation service failed")
            await db.commit()
            return {"project": project_id, "job": job.id}

    return make


async def _speaker(sessions, project_id: str, label: str, **fields) -> str:
    async with sessions() as db:
        sp = Speaker(project_id=project_id, label=label, display_name=label,
                     voice_design_prompt="A natural speaking voice", **fields)
        db.add(sp)
        await db.commit()
        return sp.id


async def _line(sessions, job_id: str, start: float, speaker_id: str | None, tts: str = "") -> str:
    async with sessions() as db:
        seg = Segment(job_id=job_id, speaker_id=speaker_id, start_time=start, end_time=start + 2,
                      source_text="你好", khmer_text="សួស្ដី", tts_audio_path=tts)
        db.add(seg)
        await db.commit()
        return seg.id


async def _resolve(sessions, segment_id: str) -> dict:
    async with sessions() as db:
        seg = (await db.execute(select(Segment).where(Segment.id == segment_id))).scalar_one()
        return await _resolve_voice(seg, db)


async def _voice_of(sessions, speaker_id: str) -> tuple:
    async with sessions() as db:
        sp = await db.get(Speaker, speaker_id)
        return sp.voice_id, sp.voice_auto


async def _voiced(sessions, ids: dict, start: float, label: str) -> str:
    """A speaker with one line in the episode, voiced once; returns its voice id."""
    speaker = await _speaker(sessions, ids["project"], label)
    await _resolve(sessions, await _line(sessions, ids["job"], start, speaker))
    return (await _voice_of(sessions, speaker))[0]


def test_pitch_tells_men_women_and_children_apart():
    assert abs(auto_voice.median_pitch(_tone(120, 1), RATE) - 120) < 5
    assert abs(auto_voice.median_pitch(_tone(220, 1), RATE) - 220) < 8
    assert auto_voice.median_pitch(np.zeros(RATE, dtype="float32"), RATE) is None
    assert [auto_voice.kind_from_pitch(hz) for hz in (120, 215, 320, None)] == ["man", "woman", "child", None]


def test_noise_is_not_a_voice():
    """Hiss or room noise used to read as ~165 Hz, right between a man and a woman."""
    noise = np.random.default_rng(1).normal(0, 0.1, RATE).astype("float32")
    assert auto_voice.median_pitch(noise, RATE) is None


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
async def test_speakers_of_an_episode_get_different_voices_spread_across_characters(episode, db_sessionmaker):
    """The second man gets another character's voice, not take B of the same character."""
    ids = await episode()
    assert await _voiced(db_sessionmaker, ids, 0.0, "SPEAKER_00") == "builtin-01-man-a"
    assert await _voiced(db_sessionmaker, ids, 0.0, "SPEAKER_01") == "builtin-03-ceo-a"
    assert await _voiced(db_sessionmaker, ids, 0.0, "SPEAKER_02") == "builtin-01-man-b"


@pytest.mark.asyncio
async def test_when_every_voice_is_taken_the_least_used_one_is_reused(episode, db_sessionmaker):
    ids = await episode()
    picks = [await _voiced(db_sessionmaker, ids, 0.0, f"SPEAKER_0{i}") for i in range(5)]
    assert picks == ["builtin-01-man-a", "builtin-03-ceo-a", "builtin-01-man-b",
                     "builtin-01-man-a", "builtin-03-ceo-a"]


@pytest.mark.asyncio
async def test_only_voices_used_in_the_same_episode_or_chosen_by_hand_count_as_taken(episode, db_sessionmaker):
    """Each episode labels unnamed characters afresh; counting the whole series ran the pool dry."""
    ep1 = await episode()
    assert await _voiced(db_sessionmaker, ep1, 0.0, "SPEAKER_00") == "builtin-01-man-a"
    ep2 = await episode(project_id=ep1["project"])
    assert await _voiced(db_sessionmaker, ep2, 0.0, "SPEAKER_05") == "builtin-01-man-a"

    # A voice the user chose stays theirs across the project
    await _speaker(db_sessionmaker, ep1["project"], "SPEAKER_09", voice_id="builtin-01-man-a", voice_auto=False)
    ep3 = await episode(project_id=ep1["project"])
    assert await _voiced(db_sessionmaker, ep3, 0.0, "SPEAKER_06") == "builtin-03-ceo-a"


@pytest.mark.asyncio
async def test_women_and_children_get_womens_and_childrens_voices(episode, db_sessionmaker):
    ids = await episode()
    assert await _voiced(db_sessionmaker, ids, 2.0, "SPEAKER_01") == "builtin-02-woman-a"
    assert await _voiced(db_sessionmaker, ids, 4.0, "SPEAKER_02") == "builtin-26-girl-7-a"


@pytest.mark.asyncio
async def test_retired_voices_are_never_picked(episode, db_sessionmaker):
    """A retired voice still installed because some speaker uses it must not be handed out again."""
    retired = "builtin-14-teen-boy-a"                     # retired in the shipped manifest
    ids = await episode(voices=[(retired, "boy", "teen", ["Student"]),
                                ("builtin-29-boy-14-c", "boy", "teen", ["Student"])])
    assert await _voiced(db_sessionmaker, ids, 4.0, "SPEAKER_02") == "builtin-29-boy-14-c"


@pytest.mark.asyncio
async def test_without_separated_vocals_the_kind_is_unknown_and_unknown_alternates(episode, db_sessionmaker):
    """The full mix (music under the dialogue) skews pitch: a woman read as a man."""
    ids = await episode(separation_failed=True)
    async with db_sessionmaker() as db:
        speaker = Speaker(project_id=ids["project"], label="SPEAKER_00")
        db.add(speaker)
        await db.flush()
        db.add(Segment(job_id=ids["job"], speaker_id=speaker.id, start_time=0.0, end_time=2.0, source_text="你"))
        await db.commit()
        assert await auto_voice._speaker_kind(db, speaker) is None
    # Unknown speakers alternate between men's and women's voices instead of all getting men's
    first = await _voiced(db_sessionmaker, ids, 0.0, "SPEAKER_01")
    second = await _voiced(db_sessionmaker, ids, 0.0, "SPEAKER_02")
    assert {first, second} == {"builtin-01-man-a", "builtin-02-woman-a"}


@pytest.mark.asyncio
async def test_lines_of_one_speaker_voiced_at_once_share_one_voice(episode, db_sessionmaker):
    ids = await episode()
    hero = await _speaker(db_sessionmaker, ids["project"], "SPEAKER_00")
    lines = [await _line(db_sessionmaker, ids["job"], 0.0, hero) for _ in range(3)]
    results = await asyncio.gather(*[_resolve(db_sessionmaker, line) for line in lines])
    assert {r["description"] for r in results} == {"builtin-01-man-a"}
    assert await _voice_of(db_sessionmaker, hero) == ("builtin-01-man-a", True)


@pytest.mark.asyncio
async def test_two_speakers_voiced_at_once_get_different_voices(episode, tmp_path):
    """On a real database with several connections (the shared-connection test pool can't race)."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/race.db", connect_args={"timeout": 30})
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        ids = await episode(sessions=sessions)
        a = await _speaker(sessions, ids["project"], "SPEAKER_00")
        b = await _speaker(sessions, ids["project"], "SPEAKER_01")
        lines = [await _line(sessions, ids["job"], 0.0, a), await _line(sessions, ids["job"], 0.0, b)]
        await asyncio.gather(*[_resolve(sessions, line) for line in lines])
        picks = {(await _voice_of(sessions, a))[0], (await _voice_of(sessions, b))[0]}
        assert picks == {"builtin-01-man-a", "builtin-03-ceo-a"}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_line_without_a_speaker_gets_a_fixed_voice_by_its_pitch(episode, db_sessionmaker):
    ids = await episode()
    line = await _line(db_sessionmaker, ids["job"], 2.0, None)        # the woman's stretch
    first, again = await _resolve(db_sessionmaker, line), await _resolve(db_sessionmaker, line)
    assert first["description"] == again["description"] == "builtin-02-woman-a"


@pytest.mark.asyncio
async def test_without_built_in_voices_the_speakers_description_is_used(episode, db_sessionmaker):
    ids = await episode(voices=[])
    hero = await _speaker(db_sessionmaker, ids["project"], "SPEAKER_00")
    kwargs = await _resolve(db_sessionmaker, await _line(db_sessionmaker, ids["job"], 0.0, hero))
    assert kwargs == {"voice_design": "A natural speaking voice"}
    assert await _voice_of(db_sessionmaker, hero) == (None, False)


@pytest.mark.asyncio
async def test_a_failing_pick_falls_back_instead_of_failing_the_line(episode, db_sessionmaker, monkeypatch):
    ids = await episode()
    hero = await _speaker(db_sessionmaker, ids["project"], "SPEAKER_00")

    async def broken(*args, **kwargs):
        raise OSError("stem unreadable")

    monkeypatch.setattr(tts_routes.auto_voice, "ensure_speaker_voice", broken)
    monkeypatch.setattr(tts_routes.auto_voice, "voice_for_line", broken)
    assert await _resolve(db_sessionmaker, await _line(db_sessionmaker, ids["job"], 0.0, hero)) == {
        "voice_design": "A natural speaking voice"}
    assert await _resolve(db_sessionmaker, await _line(db_sessionmaker, ids["job"], 0.0, None)) == {"voice_design": ""}


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
    assert (await client.patch(f"/api/v1/speakers/{hero}", json={"voice_id": "no-such-voice"})).status_code == 404


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
