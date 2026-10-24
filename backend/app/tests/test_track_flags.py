"""Voice track switches — lock and mute (hide went with the subtitles, 0.4.0) — saved per episode."""
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.models import Job
from app.services import timeline
from app.tests.timeline_helpers import _add_segment, _make_job

OFF = {"locked": False, "muted": False}


def _seg(id_, start, end, lane=0, voiced=True):
    return SimpleNamespace(id=id_, start_time=start, end_time=end, lane_index=lane,
                           tts_audio_path="x.wav" if voiced else "", tts_duration_secs=(end - start) if voiced else 0.0)


def test_only_switches_that_are_on_are_kept():
    flags = timeline.flags_from('{"0": {"locked": true, "hidden": false}, "2": {"muted": false}, "x": {"muted": true}}')
    assert flags == {0: {**OFF, "locked": True}}
    # A hide switch saved before 0.4.0 is gone: it only hid subtitles
    assert timeline.flags_from('{"0": {"hidden": true, "locked": true}, "1": {"hidden": true}}') == {0: {**OFF, "locked": True}}
    assert timeline.flags_to({}) is None
    assert json.loads(timeline.flags_to(flags)) == {"0": {**OFF, "locked": True}}
    assert timeline.flags_from(None) == {}


def test_a_clip_is_never_moved_onto_a_locked_track():
    a, b, c = _seg("a", 0, 2, 0), _seg("b", 1, 3, 0), _seg("c", 5, 6, 1)
    flags = {1: {**OFF, "locked": True}}
    timeline.settle([a, b, c], ["a"], flags)
    assert a.lane_index == 2          # track 1 had room, but it's locked


def test_a_moved_clip_stays_muted():
    a, b, c = _seg("a", 0, 2, 0), _seg("b", 1, 3, 0), _seg("c", 5, 6, 1)
    flags = {0: {**OFF, "muted": True}}
    timeline.settle([a, b, c], ["a"], flags)
    assert a.lane_index == 2          # not onto track 1, which plays
    assert flags[2] == {**OFF, "muted": True}


def test_a_leftover_hide_switch_no_longer_steers_where_clips_go():
    a, b, c = _seg("a", 0, 2, 0), _seg("b", 1, 3, 0), _seg("c", 5, 6, 1)
    flags = timeline.flags_from('{"0": {"hidden": true}}')
    timeline.settle([a, b, c], ["a"], flags)
    assert a.lane_index == 1          # track 1 is free there: nothing about it differs any more


def test_switches_of_tracks_without_clips_are_dropped():
    flags = {0: {**OFF, "locked": True}, 4: {**OFF, "muted": True}}
    assert timeline.prune_flags(flags, [_seg("a", 0, 1, 0)]) == {0: {**OFF, "locked": True}}


def test_the_export_leaves_out_muted_voices():
    a, b, c = _seg("a", 0, 1, 0), _seg("b", 1, 2, 1), _seg("c", 2, 3, 2)
    voices = timeline.export_lines([a, b, c], {1: {**OFF, "muted": True}})
    assert [s.id for s in voices] == ["a", "c"]


@pytest.mark.asyncio
async def test_track_switches_are_saved_with_the_episode(client, db_sessionmaker):
    job_id = await _make_job(client, db_sessionmaker)
    await _add_segment(db_sessionmaker, job_id, 0.0, 1.0, lane=0)
    resp = await client.put(f"/api/v1/jobs/{job_id}/track-flags",
                            json={"flags": {"0": {"muted": True}, "7": {"locked": True}}})
    assert resp.status_code == 200
    assert resp.json()["track_flags"] == {"0": {**OFF, "muted": True}}   # track 7 has no clips
    assert (await client.get(f"/api/v1/jobs/{job_id}")).json()["track_flags"] == {"0": {**OFF, "muted": True}}


@pytest.mark.asyncio
async def test_a_layout_change_carries_the_tracks_switches(client, db_sessionmaker):
    # A drop that inserts a track above track 1 moves its clips (and its lock) down
    job_id = await _make_job(client, db_sessionmaker)
    a = await _add_segment(db_sessionmaker, job_id, 0.0, 1.0, lane=0)
    b = await _add_segment(db_sessionmaker, job_id, 0.0, 1.0, lane=1)
    await client.put(f"/api/v1/jobs/{job_id}/track-flags", json={"flags": {"1": {"locked": True}}})
    resp = await client.put(f"/api/v1/jobs/{job_id}/segments/layout", json={
        "changes": [{"id": b, "lane_index": 2}, {"id": a, "lane_index": 1}],
        "track_flags": {"2": {"locked": True}},
    })
    assert resp.status_code == 200
    async with db_sessionmaker() as session:
        job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
        assert timeline.flags_from(job.track_flags) == {2: {**OFF, "locked": True}}


@pytest.mark.asyncio
async def test_tidy_leaves_switched_tracks_alone(client, db_sessionmaker):
    job_id = await _make_job(client, db_sessionmaker)
    a = await _add_segment(db_sessionmaker, job_id, 0.0, 1.0, lane=4)
    b = await _add_segment(db_sessionmaker, job_id, 0.0, 1.0, lane=0)     # locked track 0
    c = await _add_segment(db_sessionmaker, job_id, 0.5, 1.5, lane=6)    # overlaps a
    d = await _add_segment(db_sessionmaker, job_id, 2.0, 3.0, lane=9)
    await client.put(f"/api/v1/jobs/{job_id}/track-flags", json={"flags": {"0": {"locked": True}}})
    assert (await client.post(f"/api/v1/jobs/{job_id}/segments/tidy-lanes")).status_code == 200
    lanes = {s["id"]: s["lane_index"] for s in (await client.get(f"/api/v1/jobs/{job_id}/segments")).json()}
    assert lanes == {b: 0, a: 1, c: 2, d: 1}   # packed around the locked track, which keeps its clip
