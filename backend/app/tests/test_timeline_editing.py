"""
Editing clips under the 0.3.0 timeline (routes/segments.py): an edge of a
voiced clip cuts sound and never changes its speed, a speed change keeps the
same words cut, text edits keep the voice, a voiceless line follows its text,
and a clip that grows into another moves to a free track — never in time.
"""
import pytest

from app.api.routes import tts as tts_routes
from app.models.models import Segment
from app.services.timeline import overlaps
from app.tests.timeline_helpers import _add_segment, _make_job, _write_clip


async def _row(db_sessionmaker, seg_id):
    async with db_sessionmaker() as s:
        return await s.get(Segment, seg_id)


async def _voiced(db_sessionmaker, job_id, start, end, tts, lane=0, **fields):
    return await _add_segment(db_sessionmaker, job_id, start, end, lane=lane, tts_path="/nowhere/seg.wav",
                              tts_duration_secs=tts, **fields)


# ── PATCH /segments/{id} ──────────────────────────────────────

@pytest.mark.asyncio
async def test_trimming_a_voice_start_moves_its_offset_not_its_speed(client, db_sessionmaker):
    job_id = await _make_job(client, db_sessionmaker)
    sid = await _voiced(db_sessionmaker, job_id, 1.0, 3.0, 2.0)
    resp = await client.patch(f"/api/v1/segments/{sid}", json={"start_time": 1.5, "voice_offset": 0.5})
    assert resp.status_code == 200
    b = resp.json()
    assert (b["start_time"], b["voice_offset"], b["end_time"], b["voice_speed"]) == (1.5, 0.5, 3.0, 1.0)


@pytest.mark.asyncio
async def test_a_voiced_clip_cant_outgrow_its_voice(client, db_sessionmaker):
    job_id = await _make_job(client, db_sessionmaker)
    sid = await _voiced(db_sessionmaker, job_id, 1.0, 2.0, 2.0)
    b = (await client.patch(f"/api/v1/segments/{sid}", json={"end_time": 9.0})).json()
    assert b["end_time"] == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_trims_never_go_below_the_minimum_length(client, db_sessionmaker):
    job_id = await _make_job(client, db_sessionmaker)
    sid = await _voiced(db_sessionmaker, job_id, 1.0, 3.0, 2.0)
    resp = await client.patch(f"/api/v1/segments/{sid}", json={"voice_offset": 5.0})
    assert resp.status_code == 200
    b = resp.json()
    assert b["voice_offset"] <= 1.9 + 1e-9
    assert b["end_time"] - b["start_time"] >= 0.1 - 1e-9
    resp = await client.patch(f"/api/v1/segments/{sid}", json={"start_time": 2.9, "end_time": 1.0})
    assert resp.status_code == 200
    b = resp.json()
    assert b["end_time"] - b["start_time"] >= 0.1 - 1e-9


@pytest.mark.asyncio
async def test_moving_a_voiced_clip_keeps_its_length(client, db_sessionmaker):
    job_id = await _make_job(client, db_sessionmaker)
    sid = await _voiced(db_sessionmaker, job_id, 1.0, 2.5, 1.5)
    b = (await client.patch(f"/api/v1/segments/{sid}", json={"start_time": 4.0})).json()
    assert (b["start_time"], b["end_time"]) == (4.0, pytest.approx(5.5))


@pytest.mark.asyncio
async def test_changing_speed_keeps_the_same_words(client, db_sessionmaker, tmp_path, monkeypatch):
    monkeypatch.setattr(tts_routes.settings, "UPLOAD_DIR", str(tmp_path))
    job_id = await _make_job(client, db_sessionmaker)
    sid = await _add_segment(db_sessionmaker, job_id, 0.0, 1.5)
    async with db_sessionmaker() as s:
        seg = await s.get(Segment, sid)
        seg.tts_audio_path = _write_clip(tmp_path, sid, 2.0)
        seg.tts_duration_secs = 2.0
        seg.voice_offset = 0.5
        await s.commit()
    b = (await client.patch(f"/api/v1/segments/{sid}", json={"voice_speed": 1.25})).json()
    assert b["voice_speed"] == 1.25 and b["fit_rate"] == 1.0
    assert b["tts_duration_secs"] == pytest.approx(1.6, abs=0.02)
    assert b["voice_offset"] == pytest.approx(0.4, abs=0.01)
    assert b["end_time"] == pytest.approx(1.2, abs=0.02)


@pytest.mark.asyncio
async def test_editing_text_keeps_the_voice(client, db_sessionmaker):
    job_id = await _make_job(client, db_sessionmaker)
    sid = await _voiced(db_sessionmaker, job_id, 1.0, 2.0, 1.0, voice_text="ខ្មែរ")
    b = (await client.patch(f"/api/v1/segments/{sid}", json={"khmer_text": "ថ្មី"})).json()
    assert b["khmer_text"] == "ថ្មី"
    assert b["tts_audio_path"] == "/nowhere/seg.wav" and b["voice_text"] == "ខ្មែរ"
    assert (b["start_time"], b["end_time"]) == (1.0, 2.0)


@pytest.mark.asyncio
async def test_a_voiceless_line_follows_its_text(client, db_sessionmaker):
    job_id = await _make_job(client, db_sessionmaker)
    sid = await _add_segment(db_sessionmaker, job_id, 2.0, 2.5)
    b = (await client.patch(f"/api/v1/segments/{sid}", json={"khmer_text": "ក" * 30})).json()
    assert b["end_time"] == pytest.approx(3.95)
    # An end sent for a voiceless line with text is ignored
    b = (await client.patch(f"/api/v1/segments/{sid}", json={"start_time": 3.0, "end_time": 9.0})).json()
    assert (b["start_time"], b["end_time"]) == (3.0, pytest.approx(4.95))


@pytest.mark.asyncio
async def test_a_voiceless_line_with_no_text_keeps_its_length(client, db_sessionmaker):
    job_id = await _make_job(client, db_sessionmaker)
    sid = await _add_segment(db_sessionmaker, job_id, 2.0, 2.5)
    b = (await client.patch(f"/api/v1/segments/{sid}", json={"khmer_text": ""})).json()
    assert (b["start_time"], b["end_time"]) == (2.0, 2.5)


@pytest.mark.asyncio
async def test_clearing_a_voice_resets_its_window_and_follows_the_text(client, db_sessionmaker):
    job_id = await _make_job(client, db_sessionmaker)
    sid = await _voiced(db_sessionmaker, job_id, 1.0, 2.0, 2.0, voice_offset=0.5, fit_rate=1.5,
                        voice_text="x", khmer_text="ក" * 10)
    b = (await client.patch(f"/api/v1/segments/{sid}", json={"tts_audio_path": ""})).json()
    assert (b["voice_offset"], b["fit_rate"], b["voice_text"]) == (0.0, 1.0, None)
    assert b["end_time"] == pytest.approx(1.65)


@pytest.mark.asyncio
async def test_a_line_that_grows_into_its_neighbour_moves_to_another_track(client, db_sessionmaker):
    job_id = await _make_job(client, db_sessionmaker)
    a = await _add_segment(db_sessionmaker, job_id, 0.0, 0.5)
    b = await _add_segment(db_sessionmaker, job_id, 1.0, 2.0)
    body = (await client.patch(f"/api/v1/segments/{a}", json={"khmer_text": "ក" * 30})).json()
    assert (body["start_time"], body["end_time"]) == (0.0, pytest.approx(1.95))
    assert body["lane_index"] == 1
    other = await _row(db_sessionmaker, b)
    assert (other.start_time, other.end_time, other.lane_index) == (1.0, 2.0, 0)


# ── PUT /jobs/{id}/segments/layout ────────────────────────────

@pytest.mark.asyncio
async def test_layout_applies_every_change_before_settling(client, db_sessionmaker):
    job_id = await _make_job(client, db_sessionmaker)
    a = await _add_segment(db_sessionmaker, job_id, 0.0, 2.0, lane=0, khmer_text="")
    b = await _add_segment(db_sessionmaker, job_id, 0.0, 2.0, lane=1, khmer_text="")
    resp = await client.put(f"/api/v1/jobs/{job_id}/segments/layout",
                            json={"changes": [{"id": a, "lane_index": 1}, {"id": b, "lane_index": 0}]})
    assert resp.status_code == 200
    by_id = {s["id"]: s for s in resp.json()["segments"]}
    assert set(by_id) == {a, b}
    assert (by_id[a]["lane_index"], by_id[b]["lane_index"]) == (1, 0)
    ra, rb = await _row(db_sessionmaker, a), await _row(db_sessionmaker, b)
    assert (ra.lane_index, rb.lane_index) == (1, 0)
    assert (ra.start_time, ra.end_time, rb.start_time, rb.end_time) == (0.0, 2.0, 0.0, 2.0)


@pytest.mark.asyncio
async def test_layout_reports_clips_the_server_moved(client, db_sessionmaker):
    job_id = await _make_job(client, db_sessionmaker)
    a = await _voiced(db_sessionmaker, job_id, 0.0, 1.0, 1.0, lane=0)
    b = await _voiced(db_sessionmaker, job_id, 5.0, 6.0, 1.0, lane=1)
    resp = await client.put(f"/api/v1/jobs/{job_id}/segments/layout",
                            json={"changes": [{"id": a, "start_time": 5.2, "end_time": 6.2, "lane_index": 1}]})
    assert resp.status_code == 200
    segs = resp.json()["segments"]
    assert [s["id"] for s in segs] == [a]
    moved = segs[0]
    assert (moved["start_time"], moved["end_time"]) == (5.2, pytest.approx(6.2))
    assert moved["lane_index"] not in (None, 1)
    ra, rb = await _row(db_sessionmaker, a), await _row(db_sessionmaker, b)
    assert not (ra.lane_index == rb.lane_index and overlaps(ra, rb))


@pytest.mark.asyncio
async def test_layout_trims_clamp_like_a_patch(client, db_sessionmaker):
    job_id = await _make_job(client, db_sessionmaker)
    a = await _voiced(db_sessionmaker, job_id, 1.0, 2.0, 2.0)
    resp = await client.put(f"/api/v1/jobs/{job_id}/segments/layout",
                            json={"changes": [{"id": a, "end_time": 9.0}]})
    assert resp.json()["segments"][0]["end_time"] == pytest.approx(3.0)
    # Clamped back to where it already was: nothing changed, nothing reported
    resp = await client.put(f"/api/v1/jobs/{job_id}/segments/layout",
                            json={"changes": [{"id": a, "end_time": 9.0}]})
    assert resp.json()["segments"] == []


@pytest.mark.asyncio
async def test_layout_refuses_a_line_from_another_job(client, db_sessionmaker):
    job_id = await _make_job(client, db_sessionmaker)
    other_job = await _make_job(client, db_sessionmaker)
    stranger = await _add_segment(db_sessionmaker, other_job, 0.0, 1.0)
    resp = await client.put(f"/api/v1/jobs/{job_id}/segments/layout",
                            json={"changes": [{"id": stranger, "lane_index": 3}]})
    assert resp.status_code == 404
    assert (await _row(db_sessionmaker, stranger)).lane_index == 0


# ── Split and duplicate ───────────────────────────────────────

from pathlib import Path  # noqa: E402


async def _voiced_on_disk(db_sessionmaker, job_id, tmp_path, start, end, tts, **fields):
    sid = await _add_segment(db_sessionmaker, job_id, start, end, **fields)
    async with db_sessionmaker() as s:
        seg = await s.get(Segment, sid)
        seg.tts_audio_path = _write_clip(tmp_path, sid, tts)
        seg.tts_duration_secs = tts
        seg.voice_text = seg.khmer_text
        await s.commit()
    return sid


@pytest.mark.asyncio
async def test_splitting_a_voice_gives_each_half_its_own_part(client, db_sessionmaker, tmp_path):
    job_id = await _make_job(client, db_sessionmaker)
    sid = await _voiced_on_disk(db_sessionmaker, job_id, tmp_path, 0.0, 3.0, 3.0,
                                khmer_text="aaaa bbbb cccc", source_start=0.0, source_end=2.5,
                                volume_db=-3.0, voice_speed=1.1, notes="n")
    resp = await client.post(f"/api/v1/segments/{sid}/split", json={"at": 1.0})
    assert resp.status_code == 200
    f, s = resp.json()["first"], resp.json()["second"]
    assert f["id"] == sid and s["id"] != sid
    assert (f["start_time"], f["end_time"], f["voice_offset"]) == (0.0, 1.0, 0.0)
    assert (s["start_time"], s["end_time"], s["voice_offset"]) == (1.0, 3.0, 1.0)
    assert s["tts_audio_path"] != f["tts_audio_path"] and Path(s["tts_audio_path"]).exists()
    assert Path(s["tts_audio_path"]).with_name(f"seg_{s['id']}_raw.wav").exists()
    assert (f["khmer_text"], s["khmer_text"]) == ("aaaa", "bbbb cccc")
    assert f["voice_text"] == f["khmer_text"] and s["voice_text"] == s["khmer_text"]
    assert (f["source_start"], f["source_end"], s["source_start"], s["source_end"]) == (0.0, 1.0, 1.0, 2.5)
    assert (s["volume_db"], s["voice_speed"], s["notes"], s["lane_index"]) == (-3.0, 1.1, "n", f["lane_index"])


@pytest.mark.asyncio
async def test_splitting_an_outdated_voice_keeps_what_it_says(client, db_sessionmaker, tmp_path):
    job_id = await _make_job(client, db_sessionmaker)
    sid = await _voiced_on_disk(db_sessionmaker, job_id, tmp_path, 0.0, 3.0, 3.0, khmer_text="aaaa bbbb")
    async with db_sessionmaker() as s:
        seg = await s.get(Segment, sid)
        seg.khmer_text = "cccc dddd"     # edited after the voice was made
        await s.commit()
    body = (await client.post(f"/api/v1/segments/{sid}/split", json={"at": 1.5})).json()
    assert body["first"]["voice_text"] == "aaaa bbbb" and body["second"]["voice_text"] == "aaaa bbbb"


@pytest.mark.asyncio
async def test_splitting_a_voiceless_line_splits_its_text_and_lengths(client, db_sessionmaker):
    job_id = await _make_job(client, db_sessionmaker)
    text = "ក" * 20 + " " + "ខ" * 20     # 41 characters: 2.665 s
    sid = await _add_segment(db_sessionmaker, job_id, 1.0, 3.665, khmer_text=text, source_text="一二三四",
                             source_start=1.0, source_end=2.0)
    body = (await client.post(f"/api/v1/segments/{sid}/split", json={"at": 2.0})).json()
    f, s = body["first"], body["second"]
    assert (f["khmer_text"], s["khmer_text"]) == ("ក" * 20, "ខ" * 20)
    assert (f["start_time"], f["end_time"]) == (1.0, pytest.approx(2.3))
    assert (s["start_time"], s["end_time"]) == (2.0, pytest.approx(3.3))
    # The actor stopped at 2.0: the second half has no span of its own
    assert (f["source_start"], f["source_end"], s["source_start"], s["source_end"]) == (1.0, 2.0, None, None)
    # The halves overlap (each as long as its text): never on one track
    assert f["lane_index"] != s["lane_index"]


@pytest.mark.asyncio
async def test_split_too_close_to_an_edge_is_refused(client, db_sessionmaker):
    job_id = await _make_job(client, db_sessionmaker)
    sid = await _add_segment(db_sessionmaker, job_id, 1.0, 2.0, khmer_text="")
    assert (await client.post(f"/api/v1/segments/{sid}/split", json={"at": 1.05})).status_code == 422
    assert (await client.post(f"/api/v1/segments/{sid}/split", json={"at": 1.95})).status_code == 422
    assert (await client.post("/api/v1/segments/nope/split", json={"at": 1.5})).status_code == 404


@pytest.mark.asyncio
async def test_duplicating_copies_the_voice_and_finds_a_free_track(client, db_sessionmaker, tmp_path):
    job_id = await _make_job(client, db_sessionmaker)
    sid = await _voiced_on_disk(db_sessionmaker, job_id, tmp_path, 0.0, 2.0, 2.0,
                                source_start=0.0, source_end=2.0, voice_speed=1.2)
    await _add_segment(db_sessionmaker, job_id, 2.5, 3.0, khmer_text="")   # in the copy's way
    resp = await client.post(f"/api/v1/segments/{sid}/duplicate")
    assert resp.status_code == 201
    d = resp.json()
    assert d["id"] != sid
    assert (d["start_time"], d["end_time"]) == (2.0, pytest.approx(4.0))
    assert (d["source_start"], d["source_end"]) == (None, None)
    assert d["voice_speed"] == 1.2 and d["lane_index"] == 1
    assert Path(d["tts_audio_path"]).exists() and d["tts_audio_path"] != (await _row(db_sessionmaker, sid)).tts_audio_path
