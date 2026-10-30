"""Prints for a line: its voice print — ERes2NetV2 and CAM++ together (0.5.3); no face is looked for since 0.5.2.

All media is made here with ffmpeg (nothing real)."""
import subprocess

import numpy as np
import pytest

from app.core.binaries import ffmpeg
from app.services.characters import models, voiceprint
from app.services.characters.prints import LinePrints, prints_for_spans

NEEDS_FETCH = "run scripts/fetch-models.sh"


def _ff(*args):
    subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def _need(*names):
    for name in names:
        try:
            models.path(name)
        except models.ModelMissing:
            pytest.skip(NEEDS_FETCH)


@pytest.fixture
def media(tmp_path):
    """3 s of testsrc2 and stereo 44.1 kHz vocals (as separation writes them): a 2 s tone from 0.5 s."""
    video, vocals = tmp_path / "source.mp4", tmp_path / "vocals.wav"
    _ff("-f", "lavfi", "-i", "testsrc2=s=320x240:r=25:d=3", "-c:v", "mpeg4", str(video))
    _ff("-f", "lavfi", "-i", "sine=f=220:d=2", "-af", "adelay=500:all=1,apad=whole_dur=3", "-ac", "2", "-ar", "44100",
        str(vocals))
    return str(video), str(vocals)


def test_a_line_gives_one_voice_print_from_both_models(media):
    _need("eres2netv2", "campplus")
    video, vocals = media
    [p] = prints_for_spans(video, vocals, [(0.5, 2.5)])
    assert p.voice is not None and p.voice.shape == (voiceprint.SIZE,) == (384,)
    assert abs(np.linalg.norm(p.voice) - 1) < 1e-5
    assert abs(np.linalg.norm(p.voice[:192]) - 2 ** -0.5) < 1e-5     # each model's half weighs the same
    assert p.face is None and p.speaking_score is None and p.face_crop is None


def test_a_line_shorter_than_0_3_s_has_no_voice_print(media):
    video, vocals = media
    assert prints_for_spans(video, vocals, [(1.0, 1.25)])[0].voice is None


def test_no_vocals_file_gives_no_voice_print(media):
    video, _ = media
    assert prints_for_spans(video, None, [(0.5, 2.5)]) == [LinePrints(None, None, None, None)]


class _Extractor:
    """A sherpa-onnx extractor that always hears `vector`."""
    def __init__(self, vector):
        self.vector = vector

    def create_stream(self):
        class Stream:
            def accept_waveform(self, rate, samples):
                pass

            def input_finished(self):
                pass
        return Stream()

    def is_ready(self, stream):
        return True

    def compute(self, stream):
        return list(self.vector)


def test_the_voice_print_is_both_models_half_and_half(monkeypatch):
    a, b = np.zeros(192), np.zeros(192)
    a[0], b[1] = 3.0, 4.0                       # each normalised first
    monkeypatch.setattr(voiceprint, "_extractor", lambda: _Extractor(a))
    monkeypatch.setattr(voiceprint, "_campplus", lambda: _Extractor(b))
    v = voiceprint.voice_print(np.ones(16000, np.int16))
    assert v[0] == pytest.approx(2 ** -0.5) and v[192 + 1] == pytest.approx(2 ** -0.5)
    assert np.count_nonzero(v) == 2


def test_a_voice_file_is_heard_by_eres2netv2_alone(monkeypatch, tmp_path):
    """The upgrade compares Khmer voice files with the voices' reference clips: its margin was measured on ERes2NetV2."""
    a = np.zeros(192)
    a[0] = 1.0
    monkeypatch.setattr(voiceprint, "_extractor", lambda: _Extractor(a))
    monkeypatch.setattr(voiceprint, "_campplus", lambda: pytest.fail("CAM++ heard a voice file"))
    monkeypatch.setattr(voiceprint, "read_audio", lambda path: np.ones(16000, np.int16))
    assert voiceprint.file_print(str(tmp_path / "voice.wav")).shape == (192,)
