"""
Characters: the face step is switched off on a Mac too slow for it (characters.speed).

The timed workload is replaced at the service boundary (speed.measure), so no test runs a model.
"""
import json
import platform

import pytest

from app.core.config import settings
from app.services.characters import speed


@pytest.fixture(autouse=True)
def intel(monkeypatch):
    """Intel is the chip that gets measured; the Apple Silicon test says otherwise."""
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")


def parts(total: float) -> speed.Parts:
    """Parts whose raw face step takes `total` s (the voice's share is not part of it)."""
    return speed.Parts(decode=total / 4, yunet=total / 4, light_asd=total / 4, sface=total / 4, voice=999.0)


def counting(monkeypatch, total: float) -> list[int]:
    calls: list[int] = []

    def measure():
        calls.append(1)
        return parts(total)

    monkeypatch.setattr(speed, "measure", measure)
    return calls


def test_a_slow_estimate_turns_faces_off(monkeypatch):
    counting(monkeypatch, 200.0)   # x1.6 calibration: 320 s
    assert speed.face_step_enabled() is False


def test_the_calibrated_estimate_decides_and_the_budget_edge_keep_faces_on(monkeypatch):
    counting(monkeypatch, speed.FACE_STEP_BUDGET_SECS / speed.CALIBRATION)
    assert speed.face_step_enabled() is True   # the voice's 999 s don't count against it


def test_the_estimate_is_cached_per_app_version(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DUBIFY_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "DUBIFY_APP_VERSION", "1.0.0")
    calls = counting(monkeypatch, 200.0)

    assert speed.face_step_enabled() is False
    saved = json.loads((tmp_path / "character-speed.json").read_text())
    assert saved["app_version"] == "1.0.0" and saved["faces"] is False and saved["estimate_secs"] == 320.0
    assert "measured_at" in saved

    assert speed.face_step_enabled() is False
    monkeypatch.setattr(speed, "_memo", None)      # a new process reads the file
    assert speed.face_step_enabled() is False
    assert len(calls) == 1

    monkeypatch.setattr(settings, "DUBIFY_APP_VERSION", "1.0.1")
    assert speed.face_step_enabled() is False
    assert len(calls) == 2
    assert json.loads((tmp_path / "character-speed.json").read_text())["app_version"] == "1.0.1"


def test_a_corrupt_cache_is_measured_again(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DUBIFY_DATA_DIR", str(tmp_path))
    calls = counting(monkeypatch, 10.0)
    for junk in ("{not json", "[]", '{"app_version": "x"}'):
        (tmp_path / "character-speed.json").write_text(junk)
        monkeypatch.setattr(speed, "_memo", None)
        assert speed.face_step_enabled() is True
    assert len(calls) == 3
    assert json.loads((tmp_path / "character-speed.json").read_text())["faces"] is True


def test_without_a_data_folder_nothing_is_written_and_the_answer_stays_in_the_process(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "DUBIFY_DATA_DIR", "")
    calls = counting(monkeypatch, 10.0)
    assert speed.face_step_enabled() and speed.face_step_enabled()
    assert len(calls) == 1
    assert list(tmp_path.iterdir()) == []


def test_a_failed_measurement_raises(monkeypatch):
    def broken():
        raise OSError("ffmpeg missing")

    monkeypatch.setattr(speed, "measure", broken)
    with pytest.raises(OSError):
        speed.face_step_enabled()


def test_only_the_frames_inside_lines_are_counted():
    assert speed.SPEECH_FRAMES == 1950          # 3000 frames x 0.65 of the episode in lines
    assert speed.FACE_FRAMES == 1462            # x 0.5 of the time with a face x 1.5 faces
    assert speed.FEATURES == 243                # SFace on every 6th


def test_apple_silicon_keeps_faces_on_without_measuring(monkeypatch, tmp_path):
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    monkeypatch.setattr(settings, "DUBIFY_DATA_DIR", str(tmp_path))
    calls = counting(monkeypatch, 10_000.0)
    assert speed.face_step_enabled() is True and speed.face_step_enabled() is True
    assert calls == []
    assert list(tmp_path.iterdir()) == []


def test_the_timing_is_the_median_of_the_runs(monkeypatch):
    clock = iter([0.0, 1.0,  10.0, 110.0,  200.0, 203.0])   # runs of 1 s, 100 s (an outlier) and 3 s
    monkeypatch.setattr(speed.time, "perf_counter", lambda: next(clock))
    assert speed.median_secs(lambda: None, 3) == 3.0
