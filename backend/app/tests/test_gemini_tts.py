"""
The Gemini voice fallback goes through gemini_models, so a retired speech model
is replaced like any other — and newer speech models answer with a finished WAV
file instead of raw PCM. Wrapping that WAV in another WAV header would turn
every generated line into noise.
"""
import base64
import io
import wave

import httpx
import pytest

from app.services import gemini_models, tts_client as tts


def _wav_bytes(seconds: float, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(seconds * rate))
    return buf.getvalue()


def _audio_reply(data: bytes, mime: str) -> httpx.Response:
    part = {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}}
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [part]}}]},
                          request=httpx.Request("POST", "https://example.invalid"))


@pytest.fixture
def gemini_tts(monkeypatch):
    monkeypatch.setattr(tts.settings, "GEMINI_API_KEY", "test-key")
    calls = []

    def answer(reply):
        async def fake_generate(purpose, payload, *, timeout):
            calls.append(purpose)
            return reply
        monkeypatch.setattr(gemini_models, "generate_content", fake_generate)
    return answer, calls


def _duration(path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


@pytest.mark.asyncio
async def test_a_finished_wav_from_a_newer_speech_model_is_saved_as_it_is(gemini_tts, tmp_path):
    answer, calls = gemini_tts
    answer(_audio_reply(_wav_bytes(1.5), "audio/wav"))
    out = tmp_path / "line.wav"
    result = await tts.VoxCPM2Client()._gemini_synthesize("សួស្តី", str(out))
    assert result["success"] and calls == ["tts"]
    assert out.read_bytes()[:4] == b"RIFF" and out.read_bytes().count(b"RIFF") == 1
    assert _duration(out) == pytest.approx(1.5, abs=0.01)


@pytest.mark.asyncio
async def test_raw_pcm_is_still_wrapped_into_a_wav(gemini_tts, tmp_path):
    answer, _calls = gemini_tts
    pcm = b"\x01\x00" * 24000            # one second at 24 kHz
    answer(_audio_reply(pcm, "audio/L16;codec=pcm;rate=24000"))
    out = tmp_path / "line.wav"
    result = await tts.VoxCPM2Client()._gemini_synthesize("សួស្តី", str(out))
    assert result["success"]
    assert _duration(out) == pytest.approx(1.0, abs=0.01)
