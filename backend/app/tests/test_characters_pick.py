"""
Picking a character (0.5.0): POST /segments/{id}/character confirms the line for
that speaker. Until 0.5.6 a pick also translated an unedited line again for the
new speaker; that one Gemini call per pick sent the whole translation prompt
(~5,400 tokens for one line: 51 picks against 43 whole-episode translations on
the owner's data), so the owner had it removed: the Khmer stays as it is.

Translation is replaced at the service boundary (pipeline.translate_batch_with_report).
"""
import asyncio
import json

import pytest
from sqlalchemy import select

from app.models.models import Job, JobStatus, Project, Segment, Speaker
from app.services import pipeline
from app.services.translator import TranslationReport

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def gemini_configured(monkeypatch):
    monkeypatch.setattr(pipeline.settings, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(pipeline.settings, "TRANSLATION_BACKEND", "gemini")


async def _setup(db_sessionmaker, khmer="ចាស", machine="ចាស", **line_fields):
    """Two characters; one line by the first, with the given Khmer. Returns (segment id, speaker ids, job id)."""
    async with db_sessionmaker() as s:
        project = Project(name="Series", source_lang="zh", target_lang="km")
        s.add(project)
        await s.flush()
        job = Job(project_id=project.id, status=JobStatus.COMPLETED, progress=100, video_path="v.mp4")
        s.add(job)
        a = Speaker(project_id=project.id, label="SPEAKER_00", display_name="Lin")
        b = Speaker(project_id=project.id, label="SPEAKER_01", display_name="Mei")
        s.add_all([a, b])
        await s.flush()
        before = Segment(job_id=job.id, speaker_id=a.id, start_time=0.0, end_time=2.0,
                         source_text="你好", khmer_text="សួស្តី", khmer_machine="សួស្តី")
        s.add(before)
        seg = Segment(job_id=job.id, speaker_id=a.id, start_time=3.0, end_time=5.0, source_text="好的",
                      khmer_text=khmer, khmer_machine=machine, who_check=json.dumps([]), **line_fields)
        s.add(seg)
        await s.commit()
        return seg.id, {"a": a.id, "b": b.id}, job.id


def _translator(monkeypatch, text="បាទ", report=None, seen=None, gate=None, entered=None, error=None):
    async def fake(source, source_lang, target_lang, segments_context=None, story="", cast="", memory=None,
                   earlier=None):
        if seen is not None:
            seen.update(source=list(source), cast=cast, labels=[c["speaker_label"] for c in segments_context],
                        earlier=earlier)
        if entered is not None:
            entered.set()
        if gate is not None:
            await gate.wait()
        if error is not None:
            raise error
        return [text], report or TranslationReport()
    monkeypatch.setattr(pipeline, "translate_batch_with_report", fake)


async def _seg(db_sessionmaker, seg_id):
    async with db_sessionmaker() as s:
        return (await s.execute(select(Segment).where(Segment.id == seg_id)
                                .execution_options(populate_existing=True))).scalar_one()


async def test_a_pick_confirms_the_line_and_clears_its_check(client, db_sessionmaker, monkeypatch):
    seg_id, sp, _ = await _setup(db_sessionmaker)
    _translator(monkeypatch)
    r = await client.post(f"/api/v1/segments/{seg_id}/character", json={"speaker_id": sp["b"]})
    assert r.status_code == 200
    body = r.json()
    assert body["segment"]["speaker_id"] == sp["b"]
    assert body["segment"]["who_confirmed"] is True
    assert body["segment"]["who_check"] is None
    row = await _seg(db_sessionmaker, seg_id)
    assert row.speaker_id == sp["b"] and row.who_confirmed is True and row.who_check is None



async def test_a_pick_never_asks_gemini_and_keeps_the_khmer(client, db_sessionmaker, monkeypatch):
    seg_id, sp, _ = await _setup(db_sessionmaker)
    seen = {}
    _translator(monkeypatch, text="បាទ", seen=seen)
    body = (await client.post(f"/api/v1/segments/{seg_id}/character", json={"speaker_id": sp["b"]})).json()
    assert body["retranslated"] is False and not seen
    assert body["segment"]["khmer_text"] == "ចាស"
    row = await _seg(db_sessionmaker, seg_id)
    assert row.speaker_id == sp["b"] and row.who_confirmed is True and row.who_check is None
    assert row.khmer_text == row.khmer_machine == "ចាស"


async def test_picking_the_current_speaker_only_confirms_the_line(client, db_sessionmaker, monkeypatch):
    # Confirming Movelo's guess is the usual pick: the speaker doesn't change, so neither does the Khmer
    seg_id, sp, _ = await _setup(db_sessionmaker)
    seen = {}
    _translator(monkeypatch, text="បាទ", seen=seen)
    body = (await client.post(f"/api/v1/segments/{seg_id}/character", json={"speaker_id": sp["a"]})).json()
    assert body["retranslated"] is False and body["previous_khmer"] == "ចាស"
    assert not seen   # Gemini was not even asked
    row = await _seg(db_sessionmaker, seg_id)
    assert row.speaker_id == sp["a"] and row.who_confirmed is True and row.who_check is None
    assert row.khmer_text == row.khmer_machine == "ចាស"





async def test_patch_restores_who_check_for_undo(client, db_sessionmaker):
    seg_id, sp, _ = await _setup(db_sessionmaker)
    guesses = [{"speaker_id": sp["a"], "basis": "voice"}, {"speaker_id": sp["b"], "basis": "face"}]
    r = await client.patch(f"/api/v1/segments/{seg_id}", json={
        "speaker_id": sp["b"], "who_check": guesses, "who_confirmed": False})
    assert r.status_code == 200
    body = r.json()
    assert body["speaker_id"] == sp["b"] and body["who_confirmed"] is False
    assert [g["speaker_id"] for g in body["who_check"]] == [sp["a"], sp["b"]]
    # an explicit null clears the check again
    r = await client.patch(f"/api/v1/segments/{seg_id}", json={"who_check": None})
    assert r.json()["who_check"] is None
    # a change of speaker alone confirms the line
    r = await client.patch(f"/api/v1/segments/{seg_id}", json={"speaker_id": sp["a"]})
    assert r.json()["who_confirmed"] is True and r.json()["who_check"] is None


async def test_changing_a_lines_voice_is_casting_only(client, db_sessionmaker):
    seg_id, sp, _ = await _setup(db_sessionmaker)
    r = await client.patch(f"/api/v1/segments/{seg_id}", json={"voice_id": "builtin-x"})
    assert r.status_code == 200
    row = await _seg(db_sessionmaker, seg_id)
    assert row.voice_id == "builtin-x" and row.speaker_id == sp["a"]
    assert not row.who_confirmed and json.loads(row.who_check) == []





async def test_a_pick_rejects_unknown_things(client, db_sessionmaker):
    seg_id, sp, _ = await _setup(db_sessionmaker)
    assert (await client.post("/api/v1/segments/nope/character", json={"speaker_id": sp["a"]})).status_code == 404
    assert (await client.post(f"/api/v1/segments/{seg_id}/character", json={"speaker_id": "nope"})).status_code == 400
    async with db_sessionmaker() as s:
        other = Project(name="Other")
        s.add(other)
        await s.flush()
        stranger = Speaker(project_id=other.id, label="SPEAKER_00")
        s.add(stranger)
        await s.commit()
        stranger_id = stranger.id
    assert (await client.post(f"/api/v1/segments/{seg_id}/character",
                              json={"speaker_id": stranger_id})).status_code == 400
