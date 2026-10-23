"""
Timeline 0.3.1 — shaping the sound: fades on voice clips, a volume line,
music that dips under voices (ducking). The preview and the export apply the
same numbers (app/services/timeline.py ↔ frontend/src/lib/clipRules.ts,
ducking.ts). Older episodes keep sounding as they did: no fades, no ducking.
"""
import pytest
import sqlalchemy
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import app.core.database as core_db
from app.models.models import Job, Segment
from app.tests.timeline_helpers import _add_segment, _make_job


# ── Task 1: columns ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_startup_adds_fade_and_ducking_columns(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/old.db")
    try:
        async with engine.begin() as conn:
            await conn.execute(text(
                "CREATE TABLE segments (id VARCHAR PRIMARY KEY, job_id VARCHAR NOT NULL,"
                " start_time FLOAT NOT NULL, end_time FLOAT NOT NULL)"))
            await conn.execute(text("CREATE TABLE jobs (id VARCHAR PRIMARY KEY, project_id VARCHAR NOT NULL)"))
        await core_db.init_db(engine)
        async with engine.connect() as conn:
            def cols(table):
                return lambda c: {col["name"] for col in sqlalchemy.inspect(c).get_columns(table)}
            segment_columns = await conn.run_sync(cols("segments"))
            job_columns = await conn.run_sync(cols("jobs"))
        assert {"fade_in_secs", "fade_out_secs"} <= segment_columns
        assert "duck_music" in job_columns
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_segment_response_has_fades(client, db_sessionmaker):
    job_id = await _make_job(client, db_sessionmaker)
    body = (await client.post(f"/api/v1/jobs/{job_id}/segments", json={"start_time": 1.0, "end_time": 2.0})).json()
    assert (body["fade_in_secs"], body["fade_out_secs"]) == (0.0, 0.0)
    sid = await _add_segment(db_sessionmaker, job_id, 3.0, 4.0)
    async with db_sessionmaker() as s:
        seg = await s.get(Segment, sid)
        seg.fade_in_secs = seg.fade_out_secs = None     # a row from before 0.3.1
        await s.commit()
    row = [x for x in (await client.get(f"/api/v1/jobs/{job_id}/segments")).json() if x["id"] == sid][0]
    assert (row["fade_in_secs"], row["fade_out_secs"]) == (0.0, 0.0)


@pytest.mark.asyncio
async def test_ducking_is_off_for_an_older_episode_and_can_be_switched(client, db_sessionmaker):
    job_id = await _make_job(client, db_sessionmaker)
    async with db_sessionmaker() as s:
        (await s.get(Job, job_id)).duck_music = None
        await s.commit()
    assert (await client.get(f"/api/v1/jobs/{job_id}")).json()["duck_music"] is False
    resp = await client.patch(f"/api/v1/jobs/{job_id}/ducking", json={"duck_music": True})
    assert resp.status_code == 200 and resp.json()["duck_music"] is True
    assert (await client.patch("/api/v1/jobs/nope/ducking", json={"duck_music": True})).status_code == 404


# ── Task 2: the sound rules ───────────────────────────────────

from types import SimpleNamespace  # noqa: E402

from app.services.timeline import (  # noqa: E402
    DUCK_DB, DUCK_RAMP_SECS, clamp_fades, duck_gain_at, duck_spans, duck_volume_expr, fade_gain_at,
)
from app.tests.timeline_helpers import _write_clip  # noqa: E402

# (t, gain) for one voice at 1.0–2.0 s — the same table is in frontend/tests/ducking.test.ts
DUCK_TABLE = [(0.7, 1.0), (0.8, 1.0), (0.9, 0.6991), (1.0, 0.3981), (1.5, 0.3981), (2.0, 0.3981), (2.1, 0.6991), (2.2, 1.0)]


def _seg(start, end, tts=0.0, fade_in=None, fade_out=None, sid="s"):
    return SimpleNamespace(id=sid, start_time=start, end_time=end, tts_audio_path="/x.wav" if tts else "",
                           tts_duration_secs=tts, voice_offset=0.0, fit_rate=1.0, voice_speed=1.0,
                           fade_in_secs=fade_in, fade_out_secs=fade_out, lane_index=0, khmer_text="")


def test_fade_gain_is_linear_on_the_clip_as_trimmed():
    s = _seg(1.0, 3.0, tts=3.0, fade_in=0.5, fade_out=1.0)
    assert [fade_gain_at(s, t) for t in (0.9, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0)] == pytest.approx([0, 0, 0.5, 1, 1, 0.5, 0])
    assert fade_gain_at(_seg(1.0, 3.0, tts=3.0), 1.0) == 1.0     # no fade: full from the first sample


def test_fades_never_outgrow_the_clip():
    s = _seg(0.0, 1.0, tts=2.0, fade_in=0.8, fade_out=0.8)
    clamp_fades(s)
    assert (s.fade_in_secs, s.fade_out_secs) == pytest.approx((0.5, 0.5))
    s.end_time = 0.4
    clamp_fades(s)
    assert (s.fade_in_secs, s.fade_out_secs) == pytest.approx((0.2, 0.2))
    n = _seg(0.0, 1.0, tts=1.0, fade_in=-1.0)
    clamp_fades(n)
    assert (n.fade_in_secs, n.fade_out_secs) == (0.0, 0.0)


def test_duck_gain_matches_the_shared_table():
    assert DUCK_DB == -8.0 and DUCK_RAMP_SECS == 0.2
    spans = duck_spans([_seg(1.0, 2.0, tts=1.0)])
    assert spans == [(1.0, 2.0)]
    for t, g in DUCK_TABLE:
        assert duck_gain_at(spans, t) == pytest.approx(g, abs=1e-4), t


def test_close_voices_duck_as_one_span():
    segs = [_seg(2.3, 3.0, tts=1.0, sid="b"), _seg(1.0, 2.0, tts=1.0, sid="a"), _seg(5.0, 6.0, sid="voiceless")]
    assert duck_spans(segs) == [(1.0, 3.0)]
    assert duck_spans([_seg(1.0, 2.0, tts=1.0), _seg(2.5, 3.0, tts=1.0, sid="b")]) == [(1.0, 2.0), (2.5, 3.0)]
    assert duck_gain_at([], 1.0) == 1.0


def test_duck_expression_evaluates_like_duck_gain_at():
    spans = [(1.0, 2.0), (2.5, 3.25)]
    expr = duck_volume_expr(spans)
    assert duck_volume_expr([]) == "1"

    def clip(x, lo, hi):
        return max(lo, min(hi, x))
    for i in range(0, 400):
        t = i / 100
        assert eval(expr, {"clip": clip, "t": t, "__builtins__": {}}) == pytest.approx(duck_gain_at(spans, t), abs=1e-4), t


async def _voiced_line(db_sessionmaker, job_id, tmp_path, start, tts, **fields):
    sid = await _add_segment(db_sessionmaker, job_id, start, start + tts, **fields)
    async with db_sessionmaker() as s:
        seg = await s.get(Segment, sid)
        seg.tts_audio_path = _write_clip(tmp_path, sid, tts)
        seg.tts_duration_secs = tts
        await s.commit()
    return sid


@pytest.mark.asyncio
async def test_a_fade_sent_longer_than_the_clip_is_cut_to_fit(client, db_sessionmaker, tmp_path):
    job_id = await _make_job(client, db_sessionmaker)
    sid = await _voiced_line(db_sessionmaker, job_id, tmp_path, 0.0, 1.0)
    b = (await client.patch(f"/api/v1/segments/{sid}", json={"fade_in_secs": 0.8, "fade_out_secs": 0.8})).json()
    assert (b["fade_in_secs"], b["fade_out_secs"]) == pytest.approx((0.5, 0.5))
    b = (await client.patch(f"/api/v1/segments/{sid}", json={"end_time": 0.4})).json()
    assert b["fade_in_secs"] + b["fade_out_secs"] <= 0.4 + 1e-6


@pytest.mark.asyncio
async def test_split_gives_each_half_its_outer_fade(client, db_sessionmaker, tmp_path):
    job_id = await _make_job(client, db_sessionmaker)
    sid = await _voiced_line(db_sessionmaker, job_id, tmp_path, 0.0, 3.0, fade_in_secs=0.5, fade_out_secs=0.7)
    body = (await client.post(f"/api/v1/segments/{sid}/split", json={"at": 1.5})).json()
    f, s = body["first"], body["second"]
    assert (f["fade_in_secs"], f["fade_out_secs"], s["fade_in_secs"], s["fade_out_secs"]) == (0.5, 0.0, 0.0, 0.7)


@pytest.mark.asyncio
async def test_speed_change_keeps_fades_on_the_same_words(client, db_sessionmaker, tmp_path):
    job_id = await _make_job(client, db_sessionmaker)
    sid = await _voiced_line(db_sessionmaker, job_id, tmp_path, 0.0, 2.0, fade_in_secs=0.5, fade_out_secs=0.25)
    b = (await client.patch(f"/api/v1/segments/{sid}", json={"voice_speed": 1.25})).json()
    assert (b["fade_in_secs"], b["fade_out_secs"]) == pytest.approx((0.4, 0.2), abs=0.01)


@pytest.mark.asyncio
async def test_analysis_turns_ducking_on(monkeypatch, tmp_path, db_sessionmaker):
    from app.models.models import JobStatus, Project
    from app.services import pipeline
    from app.services.diarizer import DiarizedSegment
    from app.services.translator import TranslationReport

    monkeypatch.setattr(core_db, "AsyncSessionLocal", db_sessionmaker)
    monkeypatch.setattr(pipeline.settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(pipeline.settings, "GEMINI_API_KEY", "test-key")
    async with db_sessionmaker() as s:
        project = Project(name="Series")
        s.add(project)
        await s.flush()
        job = Job(project_id=project.id, status=JobStatus.STEMS_READY, progress=30, video_path="v.mp4")
        s.add(job)
        await s.flush()
        job.duck_music = None
        await s.commit()
        project_id, job_id = project.id, job.id
    d = tmp_path / project_id / job_id
    d.mkdir(parents=True)
    (d / "vocals.wav").write_bytes(b"RIFFfake")

    async def fake_transcribe(audio_path, references=(), taken_labels=(), language="zh", clip_audio=None):
        return [DiarizedSegment("SPEAKER_00", 0.0, 1.0, "unknown", "adult", "你好")], False

    async def fake_translate(texts, source_lang, target_lang, segments_context=None, story="", cast="", memory=None):
        return ["សួស្តី" for _ in texts], TranslationReport()

    monkeypatch.setattr(pipeline, "transcribe_audio", fake_transcribe)
    monkeypatch.setattr(pipeline, "translate_batch_with_report", fake_translate)
    await pipeline.run_analysis_pipeline(job_id)
    async with db_sessionmaker() as s:
        assert (await s.get(Job, job_id)).duck_music is True
