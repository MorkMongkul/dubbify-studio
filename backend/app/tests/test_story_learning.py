"""
Story notes per project: the columns installed apps get at startup, notes
learned from finished episodes, and the story API.
"""
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

import app.core.database as core_db


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
