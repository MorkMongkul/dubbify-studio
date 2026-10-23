"""
Voices that fit their line and sit in the mix like the original line.

Measured on a real project (6 episodes, the team's own voices and timings):
- The voice engine starts each voice with ~0.6 s of silence (up to 1 s) before
  the first word — the voice came in late and ran past its line. Trimmed to
  0.05 s each side, 48 of 69 voices fit their line instead of 10.
- Every voice came out at about the same level (-22..-19 dBFS) while the
  original lines ranged over 12 dB (-32..-20): the dub sat on top of the music
  in one line and under the effects in the next. Each voice now takes the
  loudness of the original line it replaces.
- 0.3.0: a new voice sets its clip's length (the clip starts where the line
  starts); a clip that then runs into the next one on its track moves to
  another track, never in time.
"""
import numpy as np
import pytest
import soundfile as sf
from sqlalchemy import select

from app.api.routes import tts as tts_routes
from app.models.models import Job, JobStatus, Segment
from app.services import voice_level
from app.services.source_separator import UNAVAILABLE_MARKER

RATE = 22050


def _tone(seconds: float, level_db: float) -> np.ndarray:
    """A 220 Hz tone whose RMS is `level_db` dBFS."""
    t = np.arange(int(RATE * seconds)) / RATE
    return (10 ** (level_db / 20) * np.sqrt(2) * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def _silence(seconds: float) -> np.ndarray:
    return np.zeros(int(RATE * seconds), dtype=np.float32)


def _write(path, *parts) -> str:
    sf.write(str(path), np.concatenate(parts), RATE)
    return str(path)


def _level(path) -> float:
    x, _ = sf.read(str(path), dtype="float32", always_2d=True)
    return voice_level.voiced_level_db(x.mean(axis=1), RATE)


# ── Trimming ──────────────────────────────────────────────────────────────────

def test_the_silence_before_and_after_the_words_is_trimmed(tmp_path):
    voice = _write(tmp_path / "v.wav", _silence(0.6), _tone(1.0, -20), _silence(0.4))
    assert voice_level.trim_silence(voice) == pytest.approx(1.1, abs=0.03)
    assert sf.info(voice).duration == pytest.approx(1.1, abs=0.03)


def test_a_voice_without_words_is_left_alone(tmp_path):
    voice = _write(tmp_path / "v.wav", _silence(0.5))
    assert voice_level.trim_silence(voice) == pytest.approx(0.5, abs=0.01)


# ── Loudness ──────────────────────────────────────────────────────────────────

def test_the_voice_takes_the_loudness_of_the_original_line(tmp_path):
    vocals = _write(tmp_path / "vocals.wav", _silence(2.0), _tone(2.0, -30), _silence(1.0))
    voice = _write(tmp_path / "v.wav", _tone(1.5, -18))
    assert voice_level.match_gain_db(voice, vocals, 2.0, 4.0) == pytest.approx(-12, abs=1)


def test_without_original_speech_the_level_is_left_alone(tmp_path):
    vocals = _write(tmp_path / "vocals.wav", _silence(5.0))
    voice = _write(tmp_path / "v.wav", _tone(1.5, -18))
    assert voice_level.match_gain_db(voice, vocals, 2.0, 4.0) == 0.0


def test_a_boost_never_clips(tmp_path):
    vocals = _write(tmp_path / "vocals.wav", _tone(3.0, -4))
    voice = _write(tmp_path / "v.wav", _tone(1.5, -14))     # +10 dB would put its peak at +3 dBFS
    gain = voice_level.match_gain_db(voice, vocals, 0.0, 3.0)
    peak = 20 * np.log10(np.abs(sf.read(voice, dtype="float32")[0]).max())
    assert peak + gain <= -1.0 + 0.05


# ── A new voice, end to end ───────────────────────────────────────────────────

@pytest.fixture
def voice_env(monkeypatch, tmp_path):
    monkeypatch.setattr(tts_routes.settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(tts_routes.settings, "GEMINI_API_KEY", "")
    monkeypatch.setattr(tts_routes.settings, "VOXCPM2_API_URL", "")
    monkeypatch.setattr(tts_routes.settings, "VOXCPM2_HF_SPACE_FALLBACK", "openbmb/VoxCPM-Demo")
    return tmp_path


async def _episode(client, db_sessionmaker, voice_env, lines, vocals_parts=None):
    """lines: (start, end); the episode's vocals hold the original lines when given."""
    project_id = (await client.post("/api/v1/projects/", json={"name": "Ep"})).json()["id"]
    async with db_sessionmaker() as s:
        job = Job(project_id=project_id, status=JobStatus.COMPLETED, progress=100)
        s.add(job)
        await s.flush()
        segs = [Segment(job_id=job.id, start_time=a, end_time=b, source_text="你好", khmer_text="សួស្តី")
                for a, b in lines]
        s.add_all(segs)
        await s.commit()
        ids = [seg.id for seg in segs]
    job_dir = voice_env / project_id / job.id
    job_dir.mkdir(parents=True, exist_ok=True)
    if vocals_parts is not None:
        _write(job_dir / "vocals.wav", *vocals_parts)
    return job_dir, ids


def _engine(*parts):
    async def fake(text: str, output_path: str = "", **kwargs) -> dict:
        _write(output_path, *parts)
        return {"success": True, "audio_path": output_path, "duration_secs": 1.0, "error": "", "backend": "voxcpm_gradio"}
    return fake


async def _segment(db_sessionmaker, seg_id) -> Segment:
    async with db_sessionmaker() as s:
        return (await s.execute(select(Segment).where(Segment.id == seg_id))).scalar_one()


@pytest.mark.asyncio
async def test_a_new_voice_starts_with_its_first_word_and_is_as_loud_as_the_original(
        client, db_sessionmaker, monkeypatch, voice_env):
    job_dir, (seg_id,) = await _episode(client, db_sessionmaker, voice_env, [(2.0, 4.0)],
                                        [_silence(2.0), _tone(2.0, -30), _silence(1.0)])
    monkeypatch.setattr(tts_routes.tts_client, "synthesize", _engine(_silence(0.6), _tone(1.5, -18), _silence(0.3)))
    assert (await client.post(f"/api/v1/tts/synthesize/segment/{seg_id}")).status_code == 200
    seg = await _segment(db_sessionmaker, seg_id)
    assert seg.tts_duration_secs == pytest.approx(1.6, abs=0.05)
    assert _level(seg.tts_audio_path) == pytest.approx(-30, abs=1.5)


@pytest.mark.asyncio
async def test_without_separated_vocals_the_voice_keeps_its_level(client, db_sessionmaker, monkeypatch, voice_env):
    """Separation failed: the 'vocals' are the whole mix, music included — nothing to match."""
    job_dir, (seg_id,) = await _episode(client, db_sessionmaker, voice_env, [(2.0, 4.0)],
                                        [_silence(2.0), _tone(2.0, -30), _silence(1.0)])
    (job_dir / UNAVAILABLE_MARKER).write_text("the separation service failed")
    monkeypatch.setattr(tts_routes.tts_client, "synthesize", _engine(_tone(1.5, -18)))
    await client.post(f"/api/v1/tts/synthesize/segment/{seg_id}")
    assert _level((await _segment(db_sessionmaker, seg_id)).tts_audio_path) == pytest.approx(-18, abs=1)


@pytest.mark.asyncio
async def test_a_new_voice_sets_the_clip_to_its_length(client, db_sessionmaker, monkeypatch, voice_env):
    job_dir, (seg_id,) = await _episode(client, db_sessionmaker, voice_env, [(1.0, 2.0)])
    async with db_sessionmaker() as s:
        seg = await s.get(Segment, seg_id)
        seg.voice_offset, seg.fit_rate = 0.3, 1.4   # left from an earlier voice
        await s.commit()
    monkeypatch.setattr(tts_routes.tts_client, "synthesize", _engine(_tone(2.4, -20)))
    assert (await client.post(f"/api/v1/tts/synthesize/segment/{seg_id}")).status_code == 200
    seg = await _segment(db_sessionmaker, seg_id)
    assert seg.end_time == pytest.approx(3.4, abs=0.05)
    assert (seg.start_time, seg.voice_offset, seg.fit_rate, seg.voice_text) == (1.0, 0.0, 1.0, "សួស្តី")


@pytest.mark.asyncio
async def test_a_short_voice_ends_where_it_ends(client, db_sessionmaker, monkeypatch, voice_env):
    job_dir, (seg_id,) = await _episode(client, db_sessionmaker, voice_env, [(1.0, 3.0)])
    monkeypatch.setattr(tts_routes.tts_client, "synthesize", _engine(_tone(0.5, -20)))
    await client.post(f"/api/v1/tts/synthesize/segment/{seg_id}")
    assert (await _segment(db_sessionmaker, seg_id)).end_time == pytest.approx(1.5, abs=0.05)


@pytest.mark.asyncio
async def test_a_new_voice_that_runs_into_the_next_line_moves_track(client, db_sessionmaker, monkeypatch, voice_env):
    job_dir, (first, second) = await _episode(client, db_sessionmaker, voice_env, [(1.0, 2.0), (2.5, 3.5)])
    monkeypatch.setattr(tts_routes.tts_client, "synthesize", _engine(_tone(2.4, -20)))
    await client.post(f"/api/v1/tts/synthesize/segment/{first}")
    a, b = await _segment(db_sessionmaker, first), await _segment(db_sessionmaker, second)
    assert (a.start_time, b.start_time, b.end_time) == (1.0, 2.5, 3.5)
    assert a.lane_index != b.lane_index


@pytest.mark.asyncio
async def test_text_edited_while_the_voice_is_made_marks_it_outdated(client, db_sessionmaker, monkeypatch, voice_env):
    job_dir, (seg_id,) = await _episode(client, db_sessionmaker, voice_env, [(1.0, 2.0)])

    async def edited_meanwhile(text: str, output_path: str = "", **kwargs) -> dict:
        async with db_sessionmaker() as s:
            seg = await s.get(Segment, seg_id)
            seg.khmer_text = "ថ្មី"
            await s.commit()
        _write(output_path, _tone(1.0, -20))
        return {"success": True, "audio_path": output_path, "duration_secs": 1.0, "error": "", "backend": "voxcpm_gradio"}

    monkeypatch.setattr(tts_routes.tts_client, "synthesize", edited_meanwhile)
    await client.post(f"/api/v1/tts/synthesize/segment/{seg_id}")
    seg = await _segment(db_sessionmaker, seg_id)
    assert (seg.voice_text, seg.khmer_text) == ("សួស្តី", "ថ្មី")


@pytest.mark.asyncio
async def test_many_new_voices_never_overlap_on_a_track(client, file_db, monkeypatch, voice_env):
    """Concurrent workers each write and settle (a real file database: see conftest.file_db)."""
    db_sessionmaker = file_db
    monkeypatch.setattr(tts_routes.settings, "TTS_BATCH_CONCURRENCY", 3)
    lines = [(i * 2.0, i * 2.0 + 1.5) for i in range(6)]
    job_dir, ids = await _episode(client, db_sessionmaker, voice_env, lines)
    monkeypatch.setattr(tts_routes.tts_client, "synthesize", _engine(_tone(3.0, -20)))
    assert (await client.post("/api/v1/tts/synthesize/batch", json={"segment_ids": ids})).status_code == 202
    async with db_sessionmaker() as s:
        rows = (await s.execute(select(Segment).where(Segment.id.in_(ids)))).scalars().all()
    assert all(r.tts_audio_path for r in rows)
    assert sorted(r.start_time for r in rows) == [a for a, _ in lines]
    for i, a in enumerate(rows):
        assert a.end_time == pytest.approx(a.start_time + 3.0, abs=0.05)
        for b in rows[i + 1:]:
            if (a.lane_index or 0) == (b.lane_index or 0):
                assert not (a.start_time < b.end_time - 0.001 and b.start_time < a.end_time - 0.001)


@pytest.mark.asyncio
async def test_a_new_voice_takes_the_loudness_of_where_the_actor_spoke(client, db_sessionmaker, monkeypatch, voice_env):
    """The clip was moved; the original line is still where the actor said it."""
    job_dir, (seg_id,) = await _episode(client, db_sessionmaker, voice_env, [(6.0, 8.0)],
                                        [_silence(2.0), _tone(2.0, -30), _silence(2.0), _tone(2.0, -10), _silence(1.0)])
    async with db_sessionmaker() as s:
        seg = await s.get(Segment, seg_id)
        seg.source_start, seg.source_end = 2.0, 4.0
        await s.commit()
    monkeypatch.setattr(tts_routes.tts_client, "synthesize", _engine(_tone(1.5, -18)))
    await client.post(f"/api/v1/tts/synthesize/segment/{seg_id}")
    assert _level((await _segment(db_sessionmaker, seg_id)).tts_audio_path) == pytest.approx(-30, abs=1.5)


# ── Clips made again from the raw voice ───────────────────────────────────────

@pytest.mark.asyncio
async def test_changing_the_volume_keeps_the_original_lines_loudness_as_the_starting_point(
        client, db_sessionmaker, monkeypatch, voice_env):
    job_dir, (seg_id,) = await _episode(client, db_sessionmaker, voice_env, [(2.0, 4.0)],
                                        [_silence(2.0), _tone(2.0, -30), _silence(1.0)])
    monkeypatch.setattr(tts_routes.tts_client, "synthesize", _engine(_tone(1.5, -18)))
    await client.post(f"/api/v1/tts/synthesize/segment/{seg_id}")
    assert (await client.patch(f"/api/v1/segments/{seg_id}", json={"volume_db": 3.0})).status_code == 200
    assert _level((await _segment(db_sessionmaker, seg_id)).tts_audio_path) == pytest.approx(-27, abs=1.5)


@pytest.mark.asyncio
async def test_a_line_moved_while_its_voice_is_made_keeps_its_new_place(client, db_sessionmaker, monkeypatch, voice_env):
    """Synthesis takes seconds to minutes and the timeline isn't locked: the clip starts where the line is now."""
    job_dir, (seg_id,) = await _episode(client, db_sessionmaker, voice_env, [(1.0, 2.0)])

    async def moved_meanwhile(text: str, output_path: str = "", **kwargs) -> dict:
        async with db_sessionmaker() as s:
            seg = (await s.execute(select(Segment).where(Segment.id == seg_id))).scalar_one()
            seg.start_time, seg.end_time = 10.0, 11.0
            await s.commit()
        _write(output_path, _tone(2.5, -20))
        return {"success": True, "audio_path": output_path, "duration_secs": 2.5, "error": "", "backend": "voxcpm_gradio"}

    monkeypatch.setattr(tts_routes.tts_client, "synthesize", moved_meanwhile)
    await client.post(f"/api/v1/tts/synthesize/segment/{seg_id}")
    seg = await _segment(db_sessionmaker, seg_id)
    assert (seg.start_time, seg.end_time) == (10.0, pytest.approx(12.5, abs=0.05))


@pytest.mark.asyncio
async def test_a_voice_made_before_0_2_8_sounds_as_it_did_after_a_volume_change(client, db_sessionmaker, voice_env):
    """Re-levelling and trimming an old voice on its first touch put it ~10 dB and 0.6 s off its untouched neighbours."""
    job_dir, (seg_id,) = await _episode(client, db_sessionmaker, voice_env, [(0.0, 2.0)],
                                        [_tone(2.0, -32), _silence(1.0)])
    tts_dir = job_dir / "tts"
    tts_dir.mkdir()
    raw = _write(tts_dir / f"seg_{seg_id}_raw.wav", _silence(0.6), _tone(1.1, -20), _silence(0.3))
    final = tts_dir / f"seg_{seg_id}.wav"
    final.write_bytes(open(raw, "rb").read())
    async with db_sessionmaker() as s:
        seg = (await s.execute(select(Segment).where(Segment.id == seg_id))).scalar_one()
        seg.tts_audio_path, seg.tts_duration_secs = str(final), 2.0
        await s.commit()
    await client.patch(f"/api/v1/segments/{seg_id}", json={"volume_db": 1.0})
    seg = await _segment(db_sessionmaker, seg_id)
    assert seg.tts_duration_secs == pytest.approx(2.0, abs=0.05)
    assert _level(seg.tts_audio_path) == pytest.approx(-19, abs=1.5)


@pytest.mark.asyncio
async def test_the_level_is_decided_once_when_the_voice_is_made(client, db_sessionmaker, monkeypatch, voice_env):
    """A line later moved over music must not be re-measured there (up to -15 dB on the next slider move)."""
    job_dir, (seg_id,) = await _episode(client, db_sessionmaker, voice_env, [(2.0, 4.0)],
                                        [_silence(2.0), _tone(2.0, -30), _silence(2.0), _tone(2.0, -50)])
    monkeypatch.setattr(tts_routes.tts_client, "synthesize", _engine(_tone(1.5, -18)))
    await client.post(f"/api/v1/tts/synthesize/segment/{seg_id}")
    assert (await _segment(db_sessionmaker, seg_id)).level_gain_db == pytest.approx(-12, abs=1)
    await client.patch(f"/api/v1/segments/{seg_id}", json={"start_time": 6.0, "end_time": 8.0})
    await client.patch(f"/api/v1/segments/{seg_id}", json={"volume_db": 2.0})
    assert _level((await _segment(db_sessionmaker, seg_id)).tts_audio_path) == pytest.approx(-28, abs=1.5)


@pytest.mark.asyncio
async def test_a_volume_boost_never_clips_a_matched_voice(client, db_sessionmaker, monkeypatch, voice_env):
    job_dir, (seg_id,) = await _episode(client, db_sessionmaker, voice_env, [(0.0, 3.0)], [_tone(3.0, -4)])
    monkeypatch.setattr(tts_routes.tts_client, "synthesize", _engine(_tone(1.5, -14)))
    await client.post(f"/api/v1/tts/synthesize/segment/{seg_id}")
    await client.patch(f"/api/v1/segments/{seg_id}", json={"volume_db": 6.0})
    clip = sf.read((await _segment(db_sessionmaker, seg_id)).tts_audio_path, dtype="float32")[0]
    assert 20 * np.log10(np.abs(clip).max()) <= -1.0 + 0.1


# ── Voices the engine sends as MP3 ────────────────────────────────────────────
# The free HF Space fallback (openbmb/VoxCPM-Demo) answers with MP3, saved under
# the clip's .wav name. 0.2.8 rewrote it in place as WAV with the MP3's own
# encoding; the write failed after opening the file, which emptied it — the
# line counted as generated, with a 0-byte clip and no waveform.

def _mp3(tmp_path, *parts) -> bytes:
    import subprocess
    from app.core.binaries import ffmpeg
    wav = _write(tmp_path / "src.wav", *parts)
    mp3 = tmp_path / "src.mp3"
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-i", wav, "-c:a", "libmp3lame", str(mp3)], check=True)
    return mp3.read_bytes()


def test_trimming_an_mp3_voice_makes_it_a_wav_and_never_empties_it(tmp_path):
    voice = tmp_path / "seg_x.wav"
    voice.write_bytes(_mp3(tmp_path, _silence(0.6), _tone(1.0, -20), _silence(0.3)))
    assert voice_level.trim_silence(str(voice)) == pytest.approx(1.1, abs=0.1)
    assert sf.info(str(voice)).format == "WAV" and sf.info(str(voice)).duration == pytest.approx(1.1, abs=0.1)


@pytest.mark.asyncio
async def test_a_voice_sent_as_mp3_is_kept(client, db_sessionmaker, monkeypatch, voice_env):
    job_dir, (seg_id,) = await _episode(client, db_sessionmaker, voice_env, [(1.0, 3.0)])
    audio = _mp3(voice_env, _silence(0.6), _tone(1.5, -20), _silence(0.3))

    async def space(text: str, output_path: str = "", **kwargs) -> dict:
        open(output_path, "wb").write(audio)
        return {"success": True, "audio_path": output_path, "duration_secs": 2.4, "error": "", "backend": "voxcpm_hf_space"}

    monkeypatch.setattr(tts_routes.tts_client, "synthesize", space)
    assert (await client.post(f"/api/v1/tts/synthesize/segment/{seg_id}")).status_code == 200
    seg = await _segment(db_sessionmaker, seg_id)
    assert seg.tts_duration_secs == pytest.approx(1.6, abs=0.1)
    assert sf.info(seg.tts_audio_path).duration == pytest.approx(1.6, abs=0.1)


@pytest.mark.asyncio
async def test_a_voice_that_comes_back_empty_is_a_failure_not_a_clip(client, db_sessionmaker, monkeypatch, voice_env):
    job_dir, (seg_id,) = await _episode(client, db_sessionmaker, voice_env, [(1.0, 3.0)])

    async def empty(text: str, output_path: str = "", **kwargs) -> dict:
        open(output_path, "wb").close()
        return {"success": True, "audio_path": output_path, "duration_secs": 1.8, "error": "", "backend": "voxcpm_hf_space"}

    monkeypatch.setattr(tts_routes.tts_client, "synthesize", empty)
    assert (await client.post(f"/api/v1/tts/synthesize/segment/{seg_id}")).status_code == 503
    seg = await _segment(db_sessionmaker, seg_id)
    assert seg.tts_audio_path == "" and seg.tts_duration_secs == 0
