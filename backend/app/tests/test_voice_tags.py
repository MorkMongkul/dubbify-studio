"""
Voice tags: who a voice sounds like (gender + age), personality and role tags,
and the Voices-tab section they put it in. Built-in voices' tags are fixed;
any voice can be copied into one of the user's own.
"""
import io
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from sqlalchemy import text

from app.api.routes import voices as voices_routes
from app.models.models import Voice
from app.services.voice_tags import voice_section


@pytest.fixture
def voices_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(voices_routes, "VOICES_DIR", tmp_path / "voices")
    return tmp_path / "voices"


def _wav() -> bytes:
    buf = io.BytesIO()
    sf.write(buf, (np.sin(np.arange(4800) / 7) * 8000).astype("int16"), 48000, format="WAV")
    return buf.getvalue()


async def _post_clone(client, **form):
    return await client.post("/api/v1/voices/", data={"name": "Mine", "mode": "clone", **form},
                             files={"reference_audio": ("ref.wav", _wav(), "audio/wav")})


async def _clone(client, **form) -> dict:
    resp = await _post_clone(client, **form)
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.parametrize("gender, age, roles, section", [
    ("man", "20s", [], "young_men"),
    ("woman", "30s", [], "young_women"),
    ("man", "50s", [], "older_men"),
    ("woman", "40s", [], "older_women"),
    ("woman", "70s+", [], "elders"),
    ("man", "60s", [], "elders"),
    ("girl", "child", [], "teens_children"),
    ("boy", None, [], "teens_children"),
    ("woman", "40s", ["Narrator"], "narrators"),
    (None, None, ["narrator"], "narrators"),
    (None, "20s", ["Lead"], None),
    ("man", None, [], "young_men"),
])
def test_voice_section_rule(gender, age, roles, section):
    assert voice_section(gender, age, roles) == section


@pytest.mark.asyncio
async def test_tags_are_cleaned_and_limited(client, voices_dir):
    vid = (await _clone(client))["id"]
    resp = await client.patch(f"/api/v1/voices/{vid}", json={"personality": ["  Warm ", "warm", "Very   gentle"]})
    assert resp.status_code == 200
    assert resp.json()["personality"] == ["Warm", "Very gentle"]
    for bad in ({"personality": [f"Tag {i}" for i in range(7)]},
                {"roles": ["x" * 30]},
                {"gender": "robot"},
                {"age_group": "25"}):
        assert (await client.patch(f"/api/v1/voices/{vid}", json=bad)).status_code == 422, bad


@pytest.mark.asyncio
async def test_create_with_tags_and_section(client, voices_dir):
    voice = await _clone(client, gender="woman", age_group="40s", personality=[" Calm", "calm", "Elegant"], roles="Empress")
    assert (voice["gender"], voice["age_group"], voice["personality"], voice["roles"], voice["section"]) == (
        "woman", "40s", ["Calm", "Elegant"], ["Empress"], "older_women")
    listed = {v["id"]: v for v in (await client.get("/api/v1/voices/")).json()}
    assert (listed[voice["id"]]["section"], listed[voice["id"]]["roles"]) == ("older_women", ["Empress"])

    assert (await _post_clone(client, name="Bad", gender="robot")).status_code == 422
    assert len((await client.get("/api/v1/voices/")).json()) == 1, "a refused voice is not saved"


@pytest.mark.asyncio
async def test_clearing_who_moves_a_voice_back_to_my_voices(client, voices_dir):
    vid = (await _clone(client, gender="man", age_group="30s", personality="Calm"))["id"]
    body = (await client.patch(f"/api/v1/voices/{vid}", json={"gender": None})).json()
    assert (body["gender"], body["section"], body["age_group"], body["personality"]) == (None, None, "30s", ["Calm"])


@pytest.mark.asyncio
async def test_tag_only_edit_never_rebakes_a_design_voice(client, monkeypatch, voices_dir):
    baked = []

    async def bake(voice, db):
        baked.append(voice.id)
        return False

    monkeypatch.setattr(voices_routes, "_bake_design_voice", bake)
    vid = (await client.post("/api/v1/voices/", data={"name": "Hero", "mode": "design", "description": "deep"})).json()["id"]
    assert baked == [vid]
    resp = await client.patch(f"/api/v1/voices/{vid}", json={"personality": ["Warm"], "gender": "man", "age_group": "20s"})
    assert resp.status_code == 200 and resp.json()["section"] == "young_men"
    assert baked == [vid], "changing only tags must not make a new sample"


@pytest.mark.asyncio
async def test_a_voice_without_tags_has_empty_tags_and_no_section(client, db_sessionmaker):
    """Voices made before tags existed: the columns are SQL NULL."""
    async with db_sessionmaker() as db:
        await db.execute(text(
            "INSERT INTO voices (id, name, mode, description, reference_audio_path, reference_transcript,"
            " cfg_value, inference_timesteps, seed) VALUES ('old-1', 'Old', 'design', '', '', '', 2.0, 10, 5)"))
        await db.commit()
    old = {v["id"]: v for v in (await client.get("/api/v1/voices/")).json()}["old-1"]
    assert (old["gender"], old["age_group"], old["personality"], old["roles"], old["section"]) == (None, None, [], [], None)


def _builtin(**overrides) -> Voice:
    fields = dict(id="builtin-00-test-a", name="Heng Dara", mode="ultimate", description="A young man.",
                  reference_audio_path="", reference_transcript="សួស្ដី", cfg_value=2.8, inference_timesteps=15,
                  seed=-1, gender="man", age_group="20s", personality=["Warm"], roles=["Lead"])
    return Voice(**(fields | overrides))


@pytest.mark.asyncio
async def test_built_in_tags_cannot_be_changed(client, db_sessionmaker):
    async with db_sessionmaker() as db:
        db.add(_builtin())
        await db.commit()
    assert (await client.patch("/api/v1/voices/builtin-00-test-a", json={"roles": ["Villain"]})).status_code == 403
    async with db_sessionmaker() as db:
        assert (await db.get(Voice, "builtin-00-test-a")).roles == ["Lead"]


@pytest.mark.asyncio
async def test_copy_has_its_own_clip_and_deleting_it_keeps_the_original(client, db_sessionmaker, voices_dir, tmp_path):
    original = tmp_path / "builtin-clip.wav"
    original.write_bytes(_wav())
    async with db_sessionmaker() as db:
        db.add(_builtin(reference_audio_path=str(original)))
        await db.commit()

    resp = await client.post("/api/v1/voices/builtin-00-test-a/copy")
    assert resp.status_code == 201, resp.text
    copy = resp.json()
    assert not copy["id"].startswith("builtin-") and copy["builtin"] is False
    assert copy["name"] == "Heng Dara (copy)"
    kept = ("mode", "description", "reference_transcript", "cfg_value", "inference_timesteps", "seed",
            "gender", "age_group", "personality", "roles", "section")
    assert {k: copy[k] for k in kept} == {
        "mode": "ultimate", "description": "A young man.", "reference_transcript": "សួស្ដី", "cfg_value": 2.8,
        "inference_timesteps": 15, "seed": -1, "gender": "man", "age_group": "20s", "personality": ["Warm"],
        "roles": ["Lead"], "section": "young_men"}
    clip = Path(copy["reference_audio_path"])
    assert clip.parent == voices_dir / copy["id"]
    assert clip.read_bytes() == original.read_bytes()

    assert (await client.delete(f"/api/v1/voices/{copy['id']}")).status_code == 204
    assert not clip.exists() and original.exists()


@pytest.mark.asyncio
async def test_copying_a_voice_without_a_clip(client, db_sessionmaker, voices_dir):
    async with db_sessionmaker() as db:
        db.add(Voice(id="plain-1", name="Plain", mode="design", description="calm", reference_audio_path=""))
        await db.commit()
    resp = await client.post("/api/v1/voices/plain-1/copy")
    assert resp.status_code == 201
    assert (resp.json()["name"], resp.json()["reference_audio_path"]) == ("Plain (copy)", "")
    assert (await client.post("/api/v1/voices/missing/copy")).status_code == 404
