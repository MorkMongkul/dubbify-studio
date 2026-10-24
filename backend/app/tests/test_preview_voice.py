"""The preview's voice at its clip's speed: the export's atempo, made once and kept."""
import io

import numpy as np
import pytest
import soundfile as sf

from app.tests.timeline_helpers import _add_segment, _make_job

SR = 48000


def _tone(path, secs=2.0, hz=440.0):
    t = np.arange(int(secs * SR)) / SR
    sf.write(str(path), (0.3 * np.sin(2 * np.pi * hz * t)).astype(np.float32), SR)
    return str(path)


def _pitch(samples, sr):
    spectrum = np.abs(np.fft.rfft(samples))
    return np.fft.rfftfreq(len(samples), 1 / sr)[int(np.argmax(spectrum))]


@pytest.mark.asyncio
async def test_a_voice_at_its_clips_speed_is_shorter_with_the_same_pitch(client, db_sessionmaker, tmp_path):
    job_id = await _make_job(client, db_sessionmaker)
    seg = await _add_segment(db_sessionmaker, job_id, 0.0, 1.6, tts_path=_tone(tmp_path / "seg_a.wav"),
                             tts_duration_secs=2.0, fit_rate=1.25)
    resp = await client.get(f"/api/v1/segments/{seg}/voice", params={"rate": 1.25})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"
    samples, sr = sf.read(io.BytesIO(resp.content))
    assert abs(len(samples) / sr - 1.6) < 0.03
    assert abs(_pitch(samples, sr) - 440) < 5          # a time-stretch, not a resample


@pytest.mark.asyncio
async def test_the_stretched_voice_is_made_once(client, db_sessionmaker, tmp_path):
    job_id = await _make_job(client, db_sessionmaker)
    seg = await _add_segment(db_sessionmaker, job_id, 0.0, 1.0, tts_path=_tone(tmp_path / "seg_b.wav"),
                             tts_duration_secs=2.0, fit_rate=2.0)
    first = await client.get(f"/api/v1/segments/{seg}/voice", params={"rate": 2.0})
    made = list((tmp_path / "preview").iterdir())
    second = await client.get(f"/api/v1/segments/{seg}/voice", params={"rate": 2.0})
    assert first.status_code == second.status_code == 200
    assert len(made) == 1 and list((tmp_path / "preview").iterdir()) == made
    assert made[0].stat().st_mtime_ns == (tmp_path / "preview" / made[0].name).stat().st_mtime_ns


@pytest.mark.asyncio
async def test_a_new_speed_replaces_the_old_copy(client, db_sessionmaker, tmp_path):
    job_id = await _make_job(client, db_sessionmaker)
    seg = await _add_segment(db_sessionmaker, job_id, 0.0, 1.0, tts_path=_tone(tmp_path / "seg_c.wav"),
                             tts_duration_secs=2.0, fit_rate=1.5)
    await client.get(f"/api/v1/segments/{seg}/voice", params={"rate": 1.5})
    await client.get(f"/api/v1/segments/{seg}/voice", params={"rate": 1.2})
    assert [p.name for p in (tmp_path / "preview").iterdir()] == [
        n for n in (p.name for p in (tmp_path / "preview").iterdir()) if "1.2000" in n]


@pytest.mark.asyncio
async def test_only_speed_ups_the_export_makes_are_served(client, db_sessionmaker, tmp_path):
    job_id = await _make_job(client, db_sessionmaker)
    seg = await _add_segment(db_sessionmaker, job_id, 0.0, 1.0, tts_path=_tone(tmp_path / "seg_d.wav"),
                             tts_duration_secs=2.0)
    for rate in (0.5, 1.0, 4.5):
        assert (await client.get(f"/api/v1/segments/{seg}/voice", params={"rate": rate})).status_code == 422
    silent = await _add_segment(db_sessionmaker, job_id, 2.0, 3.0)
    assert (await client.get(f"/api/v1/segments/{silent}/voice", params={"rate": 1.5})).status_code == 404
