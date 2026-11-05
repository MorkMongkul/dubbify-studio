"""
The Edge TTS voice of single-voice projects (0.5.7): Microsoft Edge's read-aloud service through the edge-tts package
(no key; unofficial). Edge answers MP3; the voice is saved as a real WAV. Busy or unreachable: retried with backoff,
then a clear failure — never silence. The package is replaced here; nothing reaches Microsoft.
"""
import subprocess
from pathlib import Path

import pytest

from app.core.binaries import ffmpeg
from app.services.single_voice import edge_voice

pytestmark = pytest.mark.asyncio


def _mp3(path: str) -> None:
    """What Edge would send (any audio ffmpeg reads; the bundled LGPL build may have no MP3 encoder)."""
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=220:d=0.8", "-ac", "1",
                    "-f", "wav", path], check=True)


def _edge(monkeypatch, fail_times=0, asked=None):
    calls = {"n": 0}

    class Communicate:
        def __init__(self, text, voice, rate="+0%"):
            if asked is not None:
                asked.append((text, voice, rate))

        async def save(self, path):
            calls["n"] += 1
            if calls["n"] <= fail_times:
                raise ConnectionError("Edge said no")
            _mp3(path)
    monkeypatch.setattr(edge_voice, "_communicate", Communicate)
    monkeypatch.setattr(edge_voice, "_sleep", lambda s: _nothing())
    return calls


async def _nothing():
    return None


@pytest.mark.parametrize("rate,sent", [(10, "+10%"), (-20, "-20%"), (0, "+0%")])
async def test_the_speed_is_sent_as_edges_rate(monkeypatch, tmp_path, rate, sent):
    asked = []
    _edge(monkeypatch, asked=asked)
    result = await edge_voice.synthesize("សួស្តី", str(tmp_path / "v.wav"), "km-KH-SreymomNeural", rate)
    assert result["success"] and asked == [("សួស្តី", "km-KH-SreymomNeural", sent)]


async def test_the_voice_is_saved_as_a_real_wav(monkeypatch, tmp_path):
    _edge(monkeypatch)
    out = tmp_path / "v.wav"
    result = await edge_voice.synthesize("សួស្តី", str(out), "km-KH-PisethNeural", 0)
    assert result["success"] and result["backend"] == "edge" and result["duration_secs"] > 0.5
    assert out.read_bytes()[:4] == b"RIFF"


async def test_a_busy_edge_is_tried_again(monkeypatch, tmp_path):
    calls = _edge(monkeypatch, fail_times=edge_voice.ATTEMPTS - 1)
    result = await edge_voice.synthesize("សួស្តី", str(tmp_path / "v.wav"), "km-KH-PisethNeural", 0)
    assert result["success"] and calls["n"] == edge_voice.ATTEMPTS


async def test_edge_that_never_answers_fails_clearly(monkeypatch, tmp_path):
    _edge(monkeypatch, fail_times=99)
    out = tmp_path / "v.wav"
    result = await edge_voice.synthesize("សួស្តី", str(out), "km-KH-PisethNeural", 0)
    assert not result["success"] and "Edge's voice service didn't answer" in result["error"]
    assert not out.exists()


async def test_no_text_asks_nobody(monkeypatch, tmp_path):
    asked = []
    _edge(monkeypatch, asked=asked)
    result = await edge_voice.synthesize("  ", str(tmp_path / "v.wav"), "km-KH-PisethNeural", 0)
    assert not result["success"] and asked == []
