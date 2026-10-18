"""
Built-in voices ship with the app (app/assets/builtin_voices): reference clip +
transcript, installed into the voices folder at startup, usable like any voice
but never editable or deletable.
"""
import json
import re
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.core.database as core_db
from app.core.config import settings
from app.models.models import Speaker, Voice
from app.services import builtin_voices
from app.services.tts_client import tts_client
from app.services.voice_tags import AGE_GROUPS, GENDERS, SECTIONS, normalize_tags, voice_section


def _pack(tmp_path: Path, n: int = 2) -> Path:
    assets = tmp_path / "pack"
    assets.mkdir()
    voices = []
    for i in range(n):
        vid = f"builtin-0{i}-test-a"
        sf.write(str(assets / f"{vid}.flac"), (np.sin(np.arange(4800) / 7) * 8000).astype("int16"), 48000)
        voices.append({"id": vid, "name": f"Test {i} A", "character": f"Test {i}",
                       "description": "A young woman with a bright voice.", "transcript": "សួស្ដី",
                       "file": f"{vid}.flac"})
    (assets / "manifest.json").write_text(json.dumps(
        {"version": 1, "settings": {"cfg_value": 2.8, "inference_timesteps": 15}, "voices": voices}))
    return assets


@pytest.fixture
def seeded_env(monkeypatch, db_sessionmaker, tmp_path):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(core_db, "AsyncSessionLocal", db_sessionmaker)
    return _pack(tmp_path)


@pytest.mark.asyncio
async def test_seeding_installs_each_voice_once_and_repairs_a_lost_clip(seeded_env, db_sessionmaker, tmp_path):
    assert await builtin_voices.seed_builtin_voices(seeded_env) == 2
    async with db_sessionmaker() as db:
        voices = (await db.execute(select(Voice).order_by(Voice.id))).scalars().all()
    assert [v.id for v in voices] == ["builtin-00-test-a", "builtin-01-test-a"]
    clip = Path(voices[0].reference_audio_path)
    assert clip == tmp_path / "uploads" / "voices" / "builtin-00-test-a" / "reference.wav"
    assert sf.info(str(clip)).samplerate == 48000
    assert (voices[0].mode, voices[0].reference_transcript, voices[0].cfg_value, voices[0].inference_timesteps) == (
        "ultimate", "សួស្ដី", 2.8, 15)

    assert await builtin_voices.seed_builtin_voices(seeded_env) == 0      # nothing to do
    clip.unlink()
    await builtin_voices.seed_builtin_voices(seeded_env)
    assert clip.exists()


# The voices table as 0.2.2 created it, before voices had tags.
OLD_VOICES_TABLE = """
CREATE TABLE voices (
    id VARCHAR PRIMARY KEY, name VARCHAR(120) NOT NULL, mode VARCHAR(20), description TEXT,
    reference_audio_path VARCHAR, reference_transcript TEXT, cfg_value FLOAT,
    inference_timesteps INTEGER, seed INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)
"""


@pytest.mark.asyncio
async def test_startup_adds_tag_columns_to_a_0_2_2_database(tmp_path, monkeypatch):
    """Installed apps never run alembic: starting the app must add the tag columns itself."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/old.db")
    try:
        async with engine.begin() as conn:
            await conn.execute(text(OLD_VOICES_TABLE))
            await conn.execute(text(
                "INSERT INTO voices (id, name, mode, description, reference_audio_path, reference_transcript,"
                " cfg_value, inference_timesteps, seed) VALUES ('v1', 'Narrator', 'design', '', '', '', 2.0, 10, 5)"))
        await core_db.init_db(engine)
        await core_db.init_db(engine)   # the next launch: nothing left to add, and no error
        async with engine.connect() as conn:
            columns = await conn.run_sync(lambda c: {col["name"] for col in inspect(c).get_columns("voices")})
        assert {"gender", "age_group", "personality", "roles"} <= columns

        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            v = (await db.execute(select(Voice))).scalar_one()
        assert (v.id, v.name, v.seed, v.gender, v.personality) == ("v1", "Narrator", 5, None, None)

        monkeypatch.setattr(core_db, "AsyncSessionLocal", sessions)
        monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
        assert await builtin_voices.seed_builtin_voices(_pack(tmp_path)) == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_built_in_voices_cannot_be_changed_or_deleted(client, seeded_env, db_sessionmaker):
    await builtin_voices.seed_builtin_voices(seeded_env)
    listed = (await client.get("/api/v1/voices/")).json()
    assert {v["id"]: v["builtin"] for v in listed} == {"builtin-00-test-a": True, "builtin-01-test-a": True}

    vid = "builtin-00-test-a"
    assert (await client.patch(f"/api/v1/voices/{vid}", json={"name": "Mine"})).status_code == 403
    assert (await client.delete(f"/api/v1/voices/{vid}")).status_code == 403
    files = {"reference_audio": ("x.wav", b"RIFF", "audio/wav")}
    assert (await client.post(f"/api/v1/voices/{vid}/reference", files=files)).status_code == 403
    async with db_sessionmaker() as db:
        assert (await db.get(Voice, vid)).name == "Test 0 A"


@pytest.mark.asyncio
async def test_a_speaker_keeps_its_built_in_voice_across_an_update(seeded_env, db_sessionmaker):
    """Fixed ids: re-seeding (a new app version) never orphans a speaker's choice."""
    await builtin_voices.seed_builtin_voices(seeded_env)
    async with db_sessionmaker() as db:
        from app.models.models import Project
        project = Project(name="Ep")
        db.add(project)
        await db.flush()
        db.add(Speaker(project_id=project.id, label="SPEAKER_00", voice_id="builtin-01-test-a"))
        await db.commit()
    manifest = json.loads((seeded_env / "manifest.json").read_text())
    manifest["voices"][1]["description"] = "A warmer description."
    (seeded_env / "manifest.json").write_text(json.dumps(manifest))
    assert await builtin_voices.seed_builtin_voices(seeded_env) == 1
    async with db_sessionmaker() as db:
        speaker = (await db.execute(select(Speaker))).scalar_one()
        assert speaker.voice_id == "builtin-01-test-a"
        assert (await db.get(Voice, "builtin-01-test-a")).description == "A warmer description."


@pytest.mark.asyncio
async def test_seeding_writes_tags_and_a_new_manifest_overwrites_them(seeded_env, db_sessionmaker):
    """Built-in names and tags come from the manifest, so an app update can correct them."""
    manifest = json.loads((seeded_env / "manifest.json").read_text())
    manifest["voices"][0] |= {"gender": "woman", "age_group": "20s", "personality": ["Bright"], "roles": ["Lead"]}
    (seeded_env / "manifest.json").write_text(json.dumps(manifest))
    await builtin_voices.seed_builtin_voices(seeded_env)
    async with db_sessionmaker() as db:
        tagged = await db.get(Voice, "builtin-00-test-a")
        untagged = await db.get(Voice, "builtin-01-test-a")
    assert (tagged.gender, tagged.age_group, tagged.personality, tagged.roles) == ("woman", "20s", ["Bright"], ["Lead"])
    assert (untagged.gender, untagged.personality) == (None, [])

    manifest["voices"][0] |= {"age_group": "30s", "name": "Ly Sophea"}
    (seeded_env / "manifest.json").write_text(json.dumps(manifest))
    assert await builtin_voices.seed_builtin_voices(seeded_env) == 1
    async with db_sessionmaker() as db:
        tagged = await db.get(Voice, "builtin-00-test-a")
    assert (tagged.name, tagged.age_group) == ("Ly Sophea", "30s")


@pytest.mark.asyncio
async def test_the_backup_voice_follows_the_description_when_cloning(monkeypatch):
    """Cloning sends no style text; Gemini used to fall back to a male voice for every built-in."""
    seen = {}

    async def gemini(text, output_path, voice_design=""):
        seen["voice_design"] = voice_design
        return {"success": True, "audio_path": output_path, "duration_secs": 1.0, "error": "", "backend": "gemini"}

    async def down(*args, **kwargs):
        return {"success": False, "audio_path": "", "duration_secs": 0, "error": "down", "backend": "voxcpm_hf_space"}

    monkeypatch.setattr(tts_client, "backend", "hf_space")
    monkeypatch.setattr(tts_client, "_hf_space_synthesize", down)
    monkeypatch.setattr(tts_client, "_gemini_synthesize", gemini)
    await tts_client.synthesize("សួស្ដី", voice_design="", output_path="/tmp/x.wav",
                                reference_audio_path="/v/ref.wav", reference_transcript="សួស្ដី",
                                description="An elderly woman with a soft voice.")
    assert seen["voice_design"] == "An elderly woman with a soft voice."


def test_the_shipped_library_is_complete():
    manifest = builtin_voices.load_manifest()
    voices = manifest["voices"]
    assert len(voices) >= 60
    assert len({v["id"] for v in voices}) == len(voices)
    for v in voices:
        assert builtin_voices.is_builtin(v["id"]) and v["name"] and v["transcript"] and v["description"]
        info = sf.info(str(builtin_voices.ASSETS / v["file"]))
        assert info.samplerate == 48000 and 2.0 < info.duration < 15.0
        # A person's name (Sino-Khmer, Latin letters) and the tags that sort it into a section
        assert re.fullmatch(r"[A-Z][a-z]+ [A-Z][a-z]+", v["name"]), v["name"]
        assert v.get("gender") in GENDERS and v.get("age_group") in AGE_GROUPS, v["id"]
        assert 1 <= len(v.get("personality", [])) <= 3 and 1 <= len(v.get("roles", [])) <= 2, v["id"]
        for kind in ("personality", "roles"):      # hand edits: a list of tidy tags, not "Sly" or "warm "
            assert isinstance(v[kind], list) and normalize_tags(v[kind]) == v[kind], (v["id"], kind, v[kind])
    assert len({v["name"] for v in voices}) == len(voices), "every voice has a name of its own"
    assert {voice_section(v["gender"], v["age_group"], v["roles"]) for v in voices} == set(SECTIONS)
