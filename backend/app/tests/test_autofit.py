"""The export mix must speed clips up exactly like the editor's preview
auto-fit (VideoPlayer.startTTS), or exports sound slower than editing."""
from types import SimpleNamespace

import pytest

from app.api.routes.tts import _autofit_rate


def seg(start=0.0, end=2.0, tts=None, voice_speed=1.0):
    return SimpleNamespace(
        start_time=start, end_time=end,
        tts_duration_secs=tts, voice_speed=voice_speed,
    )


def test_clip_fitting_in_window_plays_naturally():
    assert _autofit_rate(seg(tts=2.0)) == 1.0


def test_small_overflow_below_threshold_ignored():
    assert _autofit_rate(seg(tts=2.09)) == 1.0  # 1.045 < 1.05 threshold


def test_overflowing_clip_sped_up_to_fit():
    assert _autofit_rate(seg(tts=3.0)) == pytest.approx(1.5)


def test_rate_capped_at_editor_max():
    assert _autofit_rate(seg(tts=20.0)) == 3.5


def test_manual_voice_speed_opts_out():
    # Manual speed is already baked into the clip file via atempo.
    assert _autofit_rate(seg(tts=3.0, voice_speed=1.2)) == 1.0


def test_short_clip_never_slowed_down():
    assert _autofit_rate(seg(tts=1.0)) == 1.0


def test_missing_tts_duration():
    assert _autofit_rate(seg(tts=None)) == 1.0
