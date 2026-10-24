"""Media (0.4.0): the tables for a project's media, an episode's media clips and media templates."""
import pytest
import sqlalchemy
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

import app.core.database as core_db
from app.models.models import Job, MediaAsset, MediaClip, MediaTemplate
from app.tests.timeline_helpers import _make_job


async def _asset(db_sessionmaker, project_id: str) -> str:
    async with db_sessionmaker() as s:
        asset = MediaAsset(project_id=project_id, name="sponsor.mov", kind="video", path="/x/sponsor.mov",
                           width=1080, height=1920, duration_secs=7.0, has_alpha=True, alpha_kept=True,
                           has_audio=False, status="ready")
        s.add(asset)
        await s.commit()
        return asset.id


async def _clip(db_sessionmaker, job_id: str, asset_id: str) -> None:
    async with db_sessionmaker() as s:
        s.add(MediaClip(job_id=job_id, asset_id=asset_id, lane_index=0, start_time=2.0, end_time=9.0,
                        x=0.0, y=0.0, width=1.0, height=1.0))
        await s.commit()


async def _project_of(db_sessionmaker, job_id: str) -> str:
    async with db_sessionmaker() as s:
        return (await s.get(Job, job_id)).project_id


@pytest.mark.asyncio
async def test_media_rows_go_with_their_job(client, db_sessionmaker):
    job_id = await _make_job(client, db_sessionmaker)
    asset_id = await _asset(db_sessionmaker, await _project_of(db_sessionmaker, job_id))
    await _clip(db_sessionmaker, job_id, asset_id)
    assert (await client.delete(f"/api/v1/jobs/{job_id}")).status_code in (200, 204)
    async with db_sessionmaker() as s:
        assert (await s.execute(select(MediaClip))).scalars().all() == []
        assert len((await s.execute(select(MediaAsset))).scalars().all()) == 1   # the project's media stays


@pytest.mark.asyncio
async def test_media_goes_with_its_project(client, db_sessionmaker):
    job_id = await _make_job(client, db_sessionmaker)
    project_id = await _project_of(db_sessionmaker, job_id)
    asset_id = await _asset(db_sessionmaker, project_id)
    await _clip(db_sessionmaker, job_id, asset_id)
    async with db_sessionmaker() as s:
        s.add(MediaTemplate(project_id=project_id, name="Sponsor", items="[]"))
        await s.commit()
    assert (await client.delete(f"/api/v1/projects/{project_id}")).status_code in (200, 204)
    async with db_sessionmaker() as s:
        for model in (MediaAsset, MediaClip, MediaTemplate):
            assert (await s.execute(select(model))).scalars().all() == []


@pytest.mark.asyncio
async def test_old_desktop_db_gets_the_media_column(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/old.db")
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE jobs (id VARCHAR PRIMARY KEY, project_id VARCHAR NOT NULL)"))
        await core_db.init_db(engine)
        async with engine.connect() as conn:
            def cols(c):
                return {col["name"] for col in sqlalchemy.inspect(c).get_columns("jobs")}
            def tables(c):
                return set(sqlalchemy.inspect(c).get_table_names())
            assert "media_track_flags" in await conn.run_sync(cols)
            assert {"media_assets", "media_clips", "media_templates"} <= await conn.run_sync(tables)
    finally:
        await engine.dispose()
