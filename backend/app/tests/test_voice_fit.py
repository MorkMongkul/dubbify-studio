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
- A voice still longer than its line may use the silence after it, up to the
  next line — the team widened these lines by hand.
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
async def test_a_voice_longer_than_its_line_takes_the_silence_after_it(client, db_sessionmaker, monkeypatch, voice_env):
    job_dir, (first, second) = await _episode(client, db_sessionmaker, voice_env, [(1.0, 2.0), (5.0, 6.0)])
    monkeypatch.setattr(tts_routes.tts_client, "synthesize", _engine(_tone(2.5, -20)))
    await client.post(f"/api/v1/tts/synthesize/segment/{first}")
    assert (await _segment(db_sessionmaker, first)).end_time == pytest.approx(3.5, abs=0.05)
    monkeypatch.setattr(tts_routes.tts_client, "synthesize", _engine(_tone(5.0, -20)))
    await client.post(f"/api/v1/tts/synthesize/segment/{first}")
    assert (await _segment(db_sessionmaker, first)).end_time == pytest.approx(4.9, abs=0.01)   # stops before the next line
    assert (await _segment(db_sessionmaker, second)).start_time == 5.0


@pytest.mark.asyncio
async def test_a_short_voice_never_shrinks_its_line(client, db_sessionmaker, monkeypatch, voice_env):
    job_dir, (seg_id,) = await _episode(client, db_sessionmaker, voice_env, [(1.0, 3.0)])
    monkeypatch.setattr(tts_routes.tts_client, "synthesize", _engine(_tone(0.5, -20)))
    await client.post(f"/api/v1/tts/synthesize/segment/{seg_id}")
    assert (await _segment(db_sessionmaker, seg_id)).end_time == 3.0


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
    """Synthesis takes seconds to minutes and the timeline isn't locked: the room is made from where the line is now."""
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


@pytest.mark.asyncio
async def test_auto_fit_slows_a_voice_down_only_a_little(client, db_sessionmaker, monkeypatch, voice_env):
    """Trimmed voices are often shorter than their line; stretching them to 0.65× to fill it sounded wrong."""
    job_dir, (seg_id,) = await _episode(client, db_sessionmaker, voice_env, [(0.0, 2.0)])
    monkeypatch.setattr(tts_routes.tts_client, "synthesize", _engine(_tone(1.2, -20)))
    await client.post(f"/api/v1/tts/synthesize/segment/{seg_id}")
    job_id = (await _segment(db_sessionmaker, seg_id)).job_id
    assert (await client.post(f"/api/v1/jobs/{job_id}/segments/autofit")).status_code == 200
    assert (await _segment(db_sessionmaker, seg_id)).voice_speed == pytest.approx(0.9, abs=0.01)
