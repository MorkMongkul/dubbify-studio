"""
Overlay template ("brand kit") lifecycle: snapshot a job's overlays into a
global template, apply it to another episode, default-flag exclusivity, and
deletion leaving applied episodes untouched.

NOTE: never `from app.tests.conftest import ...` here — under pytest's
rootdir import mode that creates a second conftest module instance whose
engine hijacks the app's get_db override. Use the db_sessionmaker fixture.
"""
import pytest

from app.models.models import Job, JobStatus


async def _make_project(client) -> str:
    resp = await client.post("/api/v1/projects/", json={"name": "Ep"})
    assert resp.status_code == 201
    return resp.json()["id"]


async def _make_job(db_sessionmaker, project_id: str) -> str:
    async with db_sessionmaker() as session:
        job = Job(project_id=project_id, status=JobStatus.COMPLETED, progress=100)
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


async def _job_with_overlays(client, db_sessionmaker) -> str:
    """A job carrying one shape + one subtitle overlay."""
    job_id = await _make_job(db_sessionmaker, await _make_project(client))
    assert (await client.post(f"/api/v1/jobs/{job_id}/overlays/shape", json={})).status_code == 201
    assert (await client.post(f"/api/v1/jobs/{job_id}/overlays/subtitle", json={})).status_code == 201
    return job_id


@pytest.mark.asyncio
async def test_save_template_snapshots_all_overlays(client, db_sessionmaker):
    job_id = await _job_with_overlays(client, db_sessionmaker)
    resp = await client.post("/api/v1/overlay-templates", json={
        "name": "Brand kit", "from_job_id": job_id, "set_default": True,
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Brand kit"
    assert body["is_default"] is True
    assert body["item_count"] == 2


@pytest.mark.asyncio
async def test_save_template_requires_overlays(client, db_sessionmaker):
    job_id = await _make_job(db_sessionmaker, await _make_project(client))
    resp = await client.post("/api/v1/overlay-templates", json={
        "name": "Empty", "from_job_id": job_id,
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_apply_template_to_another_episode(client, db_sessionmaker):
    source_job = await _job_with_overlays(client, db_sessionmaker)
    template_id = (await client.post("/api/v1/overlay-templates", json={
        "name": "Brand kit", "from_job_id": source_job,
    })).json()["id"]

    target_job = await _make_job(db_sessionmaker, await _make_project(client))
    resp = await client.post(f"/api/v1/overlay-templates/{template_id}/apply/{target_job}")
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    listed = (await client.get(f"/api/v1/jobs/{target_job}/overlays")).json()
    assert {o["type"] for o in listed} == {"shape", "subtitle"}


@pytest.mark.asyncio
async def test_apply_skips_subtitle_when_job_already_has_one(client, db_sessionmaker):
    source_job = await _job_with_overlays(client, db_sessionmaker)
    template_id = (await client.post("/api/v1/overlay-templates", json={
        "name": "Brand kit", "from_job_id": source_job,
    })).json()["id"]

    target_job = await _make_job(db_sessionmaker, await _make_project(client))
    await client.post(f"/api/v1/jobs/{target_job}/overlays/subtitle", json={})

    resp = await client.post(f"/api/v1/overlay-templates/{template_id}/apply/{target_job}")
    assert resp.status_code == 200
    assert [o["type"] for o in resp.json()] == ["shape"]  # subtitle singleton respected

    listed = (await client.get(f"/api/v1/jobs/{target_job}/overlays")).json()
    assert sum(1 for o in listed if o["type"] == "subtitle") == 1


@pytest.mark.asyncio
async def test_default_flag_is_exclusive(client, db_sessionmaker):
    job_id = await _job_with_overlays(client, db_sessionmaker)
    first = (await client.post("/api/v1/overlay-templates", json={
        "name": "First", "from_job_id": job_id, "set_default": True,
    })).json()
    second = (await client.post("/api/v1/overlay-templates", json={
        "name": "Second", "from_job_id": job_id,
    })).json()

    resp = await client.patch(f"/api/v1/overlay-templates/{second['id']}", json={"is_default": True})
    assert resp.status_code == 200
    templates = {t["name"]: t["is_default"] for t in (await client.get("/api/v1/overlay-templates")).json()}
    assert templates == {"First": False, "Second": True}
    assert first["is_default"] is True  # was default before the switch


@pytest.mark.asyncio
async def test_delete_template_keeps_applied_overlays(client, db_sessionmaker):
    source_job = await _job_with_overlays(client, db_sessionmaker)
    template_id = (await client.post("/api/v1/overlay-templates", json={
        "name": "Brand kit", "from_job_id": source_job,
    })).json()["id"]

    target_job = await _make_job(db_sessionmaker, await _make_project(client))
    await client.post(f"/api/v1/overlay-templates/{template_id}/apply/{target_job}")

    assert (await client.delete(f"/api/v1/overlay-templates/{template_id}")).status_code == 204
    assert (await client.get("/api/v1/overlay-templates")).json() == []
    assert len((await client.get(f"/api/v1/jobs/{target_job}/overlays")).json()) == 2
