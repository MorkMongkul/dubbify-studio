"""
Story notes per project: the columns installed apps get at startup, notes
learned from finished episodes, and the story API.
"""
from datetime import datetime

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

import app.core.database as core_db
from app.core.config import settings
from app.models.models import Job, JobStatus, Project, Segment, Speaker
from app.services import story_learning, story_notes


@pytest.fixture
def background_db(monkeypatch, db_sessionmaker):
    """Background tasks open their own sessions — point them at the test DB."""
    monkeypatch.setattr(core_db, "AsyncSessionLocal", db_sessionmaker)
    return db_sessionmaker


@pytest.fixture
def gemini_key(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-key")


async def _seed_episodes(db_sessionmaker, count: int, finished=()) -> tuple[str, list[str]]:
    """A project with `count` episodes; Dr. Wang (SPEAKER_00) says one line in each."""
    async with db_sessionmaker() as s:
        project = Project(name="Hospital Hearts")
        s.add(project)
        await s.flush()
        speaker = Speaker(project_id=project.id, label="SPEAKER_00", display_name="Dr. Wang")
        s.add(speaker)
        await s.flush()
        job_ids = []
        for n in range(1, count + 1):
            job = Job(project_id=project.id, status=JobStatus.COMPLETED, title=f"Ep {n}",
                      created_at=datetime(2026, 10, 1, 12, n),
                      finished_at=datetime(2026, 10, 2) if n in finished else None)
            s.add(job)
            await s.flush()
            s.add(Segment(job_id=job.id, speaker_id=speaker.id, start_time=1.0, end_time=2.0,
                          source_text=f"第{n}集台词", khmer_text=f"ឃ្លា {n}"))
            job_ids.append(job.id)
        await s.commit()
        return project.id, job_ids


def _answer(payload: dict) -> dict:
    """What a learning request gets back: Dr. Wang, and a summary per episode asked about."""
    return {
        "tone": "Light hospital romance",
        "characters": [{"zh": "王医生", "km": "វ៉ាង", "about": "man, 30s", "speaker": "SPEAKER_00"}],
        "words": [],
        "summaries": [{"episode": n, "summary": f"Plot {n}"} for n in range(1, 10)],
    }


def _fake_gemini(monkeypatch, answer=None, before_answering=None) -> list[dict]:
    calls: list[dict] = []

    async def fake(payload):
        calls.append(payload)
        if before_answering:
            await before_answering()
        if isinstance(answer, Exception):
            raise answer
        return _answer(payload)

    monkeypatch.setattr(story_learning, "_ask_gemini", fake)
    return calls


def _prompt(payload: dict) -> str:
    return "\n".join(p.get("text", "") for c in payload["contents"] for p in c["parts"])


async def _story(client, project_id) -> dict:
    resp = await client.get(f"/api/v1/projects/{project_id}/story")
    assert resp.status_code == 200
    return resp.json()


@pytest.mark.asyncio
async def test_startup_adds_story_columns(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/old.db")
    try:
        async with engine.begin() as conn:
            await conn.execute(text(
                "CREATE TABLE projects (id VARCHAR PRIMARY KEY, name VARCHAR(255) NOT NULL, description TEXT,"
                " source_lang VARCHAR(10), target_lang VARCHAR(10), logo_path VARCHAR, created_at DATETIME,"
                " updated_at DATETIME, chat_edit_mode VARCHAR(10))"))
            await conn.execute(text(
                "CREATE TABLE jobs (id VARCHAR PRIMARY KEY, project_id VARCHAR NOT NULL, status VARCHAR(12),"
                " progress INTEGER, error_msg TEXT, title VARCHAR(255), video_path VARCHAR, audio_path VARCHAR,"
                " output_path VARCHAR, subtitle_path VARCHAR, created_at DATETIME, completed_at DATETIME,"
                " duration_secs FLOAT, bgm_volume_db FLOAT)"))
        await core_db.init_db(engine)
        async with engine.connect() as conn:
            def columns(c, table):
                return {col["name"] for col in inspect(c).get_columns(table)}
            projects = await conn.run_sync(columns, "projects")
            jobs = await conn.run_sync(columns, "jobs")
        assert "story_notes" in projects
        assert "finished_at" in jobs
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_new_project_keeps_its_intro(client, db_sessionmaker):
    resp = await client.post("/api/v1/projects/", json={"name": "Hospital Hearts", "story_intro": "  A hospital drama.  "})
    assert resp.status_code == 201
    async with db_sessionmaker() as db:
        project = await db.get(Project, resp.json()["id"])
        assert project.story_notes["intro"] == "A hospital drama."

    resp = await client.post("/api/v1/projects/", json={"name": "No intro"})
    async with db_sessionmaker() as db:
        assert (await db.get(Project, resp.json()["id"])).story_notes is None


@pytest.mark.asyncio
async def test_finishing_an_episode_learns_from_it(client, background_db, gemini_key, monkeypatch):
    project_id, (ep1, ep2) = await _seed_episodes(background_db, count=2)
    calls = _fake_gemini(monkeypatch)

    resp = await client.patch(f"/api/v1/jobs/{ep1}/finished", json={"finished": True})
    assert resp.status_code == 200 and resp.json()["finished_at"]

    story = await _story(client, project_id)
    assert [(c["zh"], c["km"], c["by"]) for c in story["characters"]] == [("王医生", "វ៉ាង", "ai")]
    first, second = story["episodes"]
    assert (first["number"], first["finished"], first["learned"], first["summary"]) == (1, True, True, "Plot 1")
    assert (second["finished"], second["learned"], second["summary"]) == (False, False, "")
    assert story["learning"] == {"state": "idle", "message": ""}
    assert len(calls) == 1
    prompt = _prompt(calls[0])
    assert "[SPEAKER_00 · Dr. Wang] 第1集台词 → ឃ្លា 1" in prompt
    assert "第2集台词" not in prompt


@pytest.mark.asyncio
async def test_first_run_reads_finished_episodes_together_in_order(client, background_db, gemini_key, monkeypatch):
    project_id, _ = await _seed_episodes(background_db, count=3, finished=(1, 2, 3))
    calls = _fake_gemini(monkeypatch)

    resp = await client.post(f"/api/v1/projects/{project_id}/story/learn")
    assert resp.status_code == 202

    assert len(calls) == 1
    prompt = _prompt(calls[0])
    assert prompt.index("=== Episode 1 ===") < prompt.index("=== Episode 2 ===") < prompt.index("=== Episode 3 ===")
    story = await _story(client, project_id)
    assert [e["summary"] for e in story["episodes"]] == ["Plot 1", "Plot 2", "Plot 3"]


@pytest.mark.asyncio
async def test_an_edit_made_while_learning_survives(client, background_db, gemini_key, monkeypatch):
    project_id, _ = await _seed_episodes(background_db, count=1, finished=(1,))

    async def user_edits_meanwhile():
        async with background_db() as s:
            project = await s.get(Project, project_id)
            project.story_notes = story_notes.apply_user_edit(
                project.story_notes, {"intro": "Typed while it learned", "words": [{"zh": "主任", "km": "ប្រធាន"}]})
            await s.commit()

    _fake_gemini(monkeypatch, before_answering=user_edits_meanwhile)
    await client.post(f"/api/v1/projects/{project_id}/story/learn")

    story = await _story(client, project_id)
    assert story["intro"] == "Typed while it learned"
    assert [(w["zh"], w["by"]) for w in story["words"]] == [("主任", "you")]
    assert [c["zh"] for c in story["characters"]] == ["王医生"]


@pytest.mark.asyncio
async def test_a_failed_run_keeps_the_notes_and_says_why(client, background_db, gemini_key, monkeypatch):
    project_id, _ = await _seed_episodes(background_db, count=1, finished=(1,))
    async with background_db() as s:
        project = await s.get(Project, project_id)
        project.story_notes = story_notes.with_intro(None, "Kept")
        await s.commit()
    _fake_gemini(monkeypatch, answer=RuntimeError("Gemini is busy"))

    await client.post(f"/api/v1/projects/{project_id}/story/learn")

    story = await _story(client, project_id)
    assert story["learning"]["state"] == "failed"
    assert "Gemini is busy" in story["learning"]["message"]
    assert story["intro"] == "Kept" and story["characters"] == []
    assert story["episodes"][0]["learned"] is False


@pytest.mark.asyncio
async def test_learning_needs_a_key(client, background_db, monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    project_id, (ep1,) = await _seed_episodes(background_db, count=1)
    calls = _fake_gemini(monkeypatch)

    assert (await client.post(f"/api/v1/projects/{project_id}/story/learn")).status_code == 400
    assert (await client.patch(f"/api/v1/jobs/{ep1}/finished", json={"finished": True})).status_code == 200

    story = await _story(client, project_id)
    assert story["can_learn"] is False and story["episodes"][0]["finished"] is True
    assert calls == []
    assert (await client.get("/api/v1/projects/no-such-project/story")).status_code == 404


@pytest.mark.asyncio
async def test_one_learning_run_at_a_time(client, background_db, gemini_key, monkeypatch):
    project_id, _ = await _seed_episodes(background_db, count=1, finished=(1,))
    _fake_gemini(monkeypatch)
    assert story_learning.claim(project_id)
    assert (await client.post(f"/api/v1/projects/{project_id}/story/learn")).status_code == 409


@pytest.mark.asyncio
async def test_unfinishing_does_not_unlearn(client, background_db, gemini_key, monkeypatch):
    project_id, (ep1,) = await _seed_episodes(background_db, count=1)
    _fake_gemini(monkeypatch)
    await client.patch(f"/api/v1/jobs/{ep1}/finished", json={"finished": True})

    resp = await client.patch(f"/api/v1/jobs/{ep1}/finished", json={"finished": False})
    assert resp.status_code == 200 and resp.json()["finished_at"] is None

    episode = (await _story(client, project_id))["episodes"][0]
    assert (episode["finished"], episode["learned"], episode["summary"]) == (False, True, "Plot 1")


@pytest.mark.asyncio
async def test_story_edit_marks_items_as_yours(client, background_db, gemini_key, monkeypatch):
    project_id, _ = await _seed_episodes(background_db, count=1, finished=(1,))
    _fake_gemini(monkeypatch)
    await client.post(f"/api/v1/projects/{project_id}/story/learn")
    character = (await _story(client, project_id))["characters"][0]

    resp = await client.patch(f"/api/v1/projects/{project_id}/story",
                              json={"characters": [{**character, "km": "វ៉ាងថ្មី"}], "tone": "Bright"})
    assert resp.status_code == 200
    edited = resp.json()
    assert (edited["characters"][0]["km"], edited["characters"][0]["by"]) == ("វ៉ាងថ្មី", "you")
    assert edited["tone"] == {"text": "Bright", "by": "you"}

    too_long = await client.patch(f"/api/v1/projects/{project_id}/story", json={"intro": "x" * 4001})
    assert too_long.status_code == 422


@pytest.mark.asyncio
async def test_translation_prompt_carries_the_story(monkeypatch):
    from app.services import translator
    prompts: list[str] = []

    async def fake_post(payload, timeout):
        prompts.append(payload["contents"][0]["parts"][0]["text"])
        raise translator.GeminiError("captured", transient=False)

    monkeypatch.setattr(translator, "_post_translation", fake_post)
    ctx = [{"speaker_label": "SPEAKER_00", "source_text": "你好"}]
    block = story_notes.for_translation(story_notes.with_intro(None, "A hospital drama."), "j1", ["j1"])
    for story in (block, ""):
        with pytest.raises(translator.GeminiError):
            await translator._translate_batch_with_gemini(["你好"], "zh", "km", ctx, story)

    with_story, without = prompts
    assert "Intro: A hospital drama." in with_story
    assert with_story.index("ABOUT THIS DRAMA") < with_story.index("STEP 1")
    assert "use it instead of guessing" in with_story
    assert "ABOUT THIS DRAMA" not in without
