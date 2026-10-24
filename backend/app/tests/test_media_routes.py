"""Media (0.4.0): the media library, an episode's media clips and media templates, through the API."""
import io

import pytest
from PIL import Image

import app.core.database as core_db
from app.models.models import Job, Overlay
from app.tests.timeline_helpers import _make_job


@pytest.fixture(autouse=True)
def _background_uses_the_test_db(monkeypatch, db_sessionmaker):
    """An upload prepares its files in the background, which opens its own sessions."""
    monkeypatch.setattr(core_db, "AsyncSessionLocal", db_sessionmaker)


def _png(w=40, h=20) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (w, h), (255, 0, 0, 128)).save(buf, format="PNG")
    return buf.getvalue()


async def _job(client, db_sessionmaker, secs: float, project_id: str | None = None) -> tuple[str, str]:
    """An episode `secs` long (in a new project, or in `project_id`)."""
    if project_id:
        async with db_sessionmaker() as s:
            job = Job(project_id=project_id, status="completed", progress=100, duration_secs=secs)
            s.add(job)
            await s.commit()
            return job.id, project_id
    job_id = await _make_job(client, db_sessionmaker)
    async with db_sessionmaker() as s:
        job = await s.get(Job, job_id)
        job.duration_secs = secs
        await s.commit()
        return job_id, job.project_id


async def _upload(client, project_id: str, name="logo.png", data=None) -> dict:
    resp = await client.post(f"/api/v1/projects/{project_id}/media", files={"file": (name, data or _png(), "image/png")})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _clip(asset_id, start, end, lane=0, **kw):
    return {"asset_id": asset_id, "lane_index": lane, "start_time": start, "end_time": end, "source_in": 0.0,
            "x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0, "volume_db": 0.0, **kw}


@pytest.mark.asyncio
async def test_an_uploaded_image_is_ready_in_the_library(client, db_sessionmaker):
    _, project_id = await _job(client, db_sessionmaker, 88.0)
    asset = await _upload(client, project_id)
    listed = (await client.get(f"/api/v1/projects/{project_id}/media")).json()
    assert [a["id"] for a in listed] == [asset["id"]]
    assert listed[0]["status"] == "ready" and listed[0]["kind"] == "image" and listed[0]["has_alpha"]
    assert listed[0]["thumb_url"].startswith("/uploads/") and listed[0]["url"].startswith("/uploads/")
    assert "path" not in listed[0]                                     # no server paths


@pytest.mark.asyncio
async def test_clips_overlapping_on_one_track_are_refused(client, db_sessionmaker):
    job_id, project_id = await _job(client, db_sessionmaker, 88.0)
    a = (await _upload(client, project_id))["id"]
    resp = await client.put(f"/api/v1/jobs/{job_id}/media-clips", json={"clips": [_clip(a, 2, 9), _clip(a, 8, 10)]})
    assert resp.status_code == 422 and "overlap" in resp.text


@pytest.mark.asyncio
async def test_where_a_clip_was_placed_is_remembered_by_its_media(client, db_sessionmaker):
    job_id, project_id = await _job(client, db_sessionmaker, 88.0)
    a = (await _upload(client, project_id))["id"]
    await client.put(f"/api/v1/jobs/{job_id}/media-clips", json={"clips": [_clip(a, 2, 9)]})
    clips = (await client.get(f"/api/v1/jobs/{job_id}/media-clips")).json()["clips"]
    moved = {**clips[0], "x": 0.1, "y": 0.7, "width": 0.5, "height": 0.2}
    await client.put(f"/api/v1/jobs/{job_id}/media-clips", json={"clips": [moved]})
    [asset] = (await client.get(f"/api/v1/projects/{project_id}/media")).json()
    assert asset["last_box"] == {"x": 0.1, "y": 0.7, "width": 0.5, "height": 0.2}


@pytest.mark.asyncio
async def test_deleting_media_removes_its_clips_everywhere_and_templates_skip_it(client, db_sessionmaker):
    job1, project_id = await _job(client, db_sessionmaker, 88.0)
    job2, _ = await _job(client, db_sessionmaker, 88.0, project_id)
    a = (await _upload(client, project_id))["id"]
    keep = (await _upload(client, project_id, name="other.png"))["id"]
    await client.put(f"/api/v1/jobs/{job1}/media-clips", json={"clips": [_clip(a, 2, 9), _clip(keep, 20, 25)]})
    await client.put(f"/api/v1/jobs/{job2}/media-clips", json={"clips": [_clip(a, 30, 35)]})
    template = (await client.post(f"/api/v1/projects/{project_id}/media-templates",
                                  json={"job_id": job1, "name": "Sponsor"})).json()
    gone = (await client.delete(f"/api/v1/media/{a}")).json()
    assert gone == {"removed_clips": 2, "episodes": 2}
    assert [c["asset_id"] for c in (await client.get(f"/api/v1/jobs/{job1}/media-clips")).json()["clips"]] == [keep]
    assert (await client.get(f"/api/v1/jobs/{job2}/media-clips")).json()["clips"] == []
    applied = (await client.post(f"/api/v1/media-templates/{template['id']}/apply/{job2}")).json()
    assert applied["skipped"] == 0 and [c["asset_id"] for c in applied["clips"]] == [keep]


@pytest.mark.asyncio
async def test_a_template_fits_a_shorter_episode_and_never_covers_its_clips(client, db_sessionmaker):
    long_job, project_id = await _job(client, db_sessionmaker, 120.0)
    short_job, _ = await _job(client, db_sessionmaker, 88.0, project_id)
    a = (await _upload(client, project_id))["id"]
    await client.put(f"/api/v1/jobs/{long_job}/media-clips", json={"clips": [_clip(a, 100, 107)]})
    template = (await client.post(f"/api/v1/projects/{project_id}/media-templates",
                                  json={"job_id": long_job, "name": "End card"})).json()
    await client.put(f"/api/v1/jobs/{short_job}/media-clips", json={"clips": [_clip(a, 80, 86)]})
    applied = (await client.post(f"/api/v1/media-templates/{template['id']}/apply/{short_job}")).json()
    placed = sorted((c["start_time"], c["end_time"], c["lane_index"]) for c in applied["clips"])
    assert placed == [(80.0, 86.0, 0), (81.0, 88.0, 1)]


@pytest.mark.asyncio
async def test_templates_are_listed_renamed_and_deleted(client, db_sessionmaker):
    job_id, project_id = await _job(client, db_sessionmaker, 88.0)
    t = (await client.post(f"/api/v1/projects/{project_id}/media-templates", json={"job_id": job_id, "name": "A"})).json()
    assert (await client.patch(f"/api/v1/media-templates/{t['id']}", json={"name": "Sponsor"})).json()["name"] == "Sponsor"
    assert [x["name"] for x in (await client.get(f"/api/v1/projects/{project_id}/media-templates")).json()] == ["Sponsor"]
    assert (await client.delete(f"/api/v1/media-templates/{t['id']}")).status_code == 204
    assert (await client.get(f"/api/v1/projects/{project_id}/media-templates")).json() == []


@pytest.mark.asyncio
async def test_an_animated_webp_or_another_file_is_refused(client, db_sessionmaker):
    _, project_id = await _job(client, db_sessionmaker, 88.0)
    frames = [Image.new("RGBA", (16, 16), (255, 0, 0, 255)), Image.new("RGBA", (16, 16), (0, 0, 255, 255))]
    buf = io.BytesIO()
    frames[0].save(buf, format="WEBP", save_all=True, append_images=frames[1:], duration=100)
    resp = await client.post(f"/api/v1/projects/{project_id}/media", files={"file": ("s.webp", buf.getvalue(), "image/webp")})
    assert resp.status_code == 415 and "GIF" in resp.text
    resp = await client.post(f"/api/v1/projects/{project_id}/media", files={"file": ("notes.txt", b"hi", "text/plain")})
    assert resp.status_code == 415
    assert (await client.get(f"/api/v1/projects/{project_id}/media")).json() == []


@pytest.mark.asyncio
async def test_an_episode_with_old_overlays_says_so(client, db_sessionmaker):
    job_id, _ = await _job(client, db_sessionmaker, 88.0)
    assert (await client.get(f"/api/v1/jobs/{job_id}/media-clips")).json()["old_overlays"] is False
    async with db_sessionmaker() as s:
        s.add(Overlay(job_id=job_id, type="shape", blur=True))
        await s.commit()
    assert (await client.get(f"/api/v1/jobs/{job_id}/media-clips")).json()["old_overlays"] is True


@pytest.mark.asyncio
async def test_media_track_switches_are_kept_for_tracks_with_clips(client, db_sessionmaker):
    job_id, project_id = await _job(client, db_sessionmaker, 88.0)
    a = (await _upload(client, project_id))["id"]
    resp = await client.put(f"/api/v1/jobs/{job_id}/media-clips", json={
        "clips": [_clip(a, 2, 9, lane=1)], "track_flags": {"1": {"hidden": True}, "4": {"locked": True}}})
    assert resp.json()["track_flags"] == {"1": {"locked": False, "hidden": True, "muted": False}}


@pytest.mark.asyncio
async def test_the_library_says_how_many_episodes_use_each_media(client, db_sessionmaker):
    job1, project_id = await _job(client, db_sessionmaker, 88.0)
    job2, _ = await _job(client, db_sessionmaker, 88.0, project_id)
    a = (await _upload(client, project_id))["id"]
    unused = (await _upload(client, project_id, name="spare.png"))["id"]
    await client.put(f"/api/v1/jobs/{job1}/media-clips", json={"clips": [_clip(a, 2, 9), _clip(a, 20, 25)]})
    await client.put(f"/api/v1/jobs/{job2}/media-clips", json={"clips": [_clip(a, 30, 35)]})
    used = {m["id"]: m["used_in"] for m in (await client.get(f"/api/v1/projects/{project_id}/media")).json()}
    assert used == {a: 2, unused: 0}

