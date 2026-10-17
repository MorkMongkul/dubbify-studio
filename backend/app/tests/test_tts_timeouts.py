"""
gradio_client has no overall time limit: a voice server that stays connected
but never answers used to hold a line (and a batch worker) forever.
"""
import time

import pytest

from app.services import tts_client as tts_module


def _hang(*args, **kwargs):
    time.sleep(0.5)
    raise AssertionError("should have been abandoned")


@pytest.mark.asyncio
async def test_a_silent_voice_server_times_out_and_is_skipped_for_a_while(monkeypatch):
    client = tts_module.VoxCPM2Client()
    client.timeout = 0.05
    monkeypatch.setattr(client, "_hf_space_call", _hang)
    result = await client._hf_space_synthesize("សួស្តី", "", "/tmp/x.wav", 2.0, "", "")
    assert not result["success"] and "timed out" in result["error"]

    # The next line doesn't wait on it again.
    started = time.monotonic()
    again = await client._hf_space_synthesize("សួស្តី", "", "/tmp/x.wav", 2.0, "", "")
    assert "cooling down" in again["error"] and time.monotonic() - started < 0.05


@pytest.mark.asyncio
async def test_a_silent_colab_server_times_out(monkeypatch):
    client = tts_module.VoxCPM2Client()
    client.timeout = 0.05
    monkeypatch.setattr(client, "_gradio_call", _hang)
    result = await client._gradio_synthesize("សួស្តី", "", "/tmp/x.wav", 2.0, 10, "", "", -1)
    assert not result["success"] and client._is_connect_error(result["error"])
